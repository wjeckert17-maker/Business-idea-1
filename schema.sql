-- =============================================================================
-- Course-planning data model — PostgreSQL 15+
--
-- Conventions
--   * bigint identity surrogate keys everywhere; natural keys are UNIQUE constraints.
--   * text + CHECK instead of ENUM types (ENUMs can't drop values and are awkward to alter).
--   * timestamptz for instants; date / time (no tz) for institution-local calendar values,
--     interpreted in institution.timezone.
--   * Ranges (daterange, int4range, tstzrange) wherever "overlap" is the real question.
--   * Tables that are derived from other tables are marked DERIVED and kept in sync by
--     triggers defined in this file. They exist only to make one query fast.
--   * See DESIGN_NOTES.md for the rationale behind each denormalization and index,
--     and for the list of places the requirements were underspecified.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- GiST indexes/exclusions mixing = with &&
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy instructor-name matching

-- Minutes after local midnight. Used to turn time-of-day into an int4range so GiST can index it.
CREATE FUNCTION minute_of_day(t time) RETURNS int
  LANGUAGE sql IMMUTABLE RETURNS NULL ON NULL INPUT
  AS $$ SELECT (EXTRACT(hour FROM t) * 60 + EXTRACT(minute FROM t))::int $$;

-- =============================================================================
-- 1. Institution and calendar
-- =============================================================================

CREATE TABLE institution (
  institution_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug            text        NOT NULL UNIQUE,
  name            text        NOT NULL,
  timezone        text        NOT NULL,          -- IANA name, e.g. 'America/Chicago'
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- A catalog year is the unit a student is "bound to". Programs are versioned by it and
-- course codes/titles/credits are versioned by it.
CREATE TABLE catalog_year (
  catalog_year_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint    NOT NULL REFERENCES institution,
  label           text      NOT NULL,            -- '2024-2025'
  effective       daterange NOT NULL,            -- dates this catalog governs
  UNIQUE (institution_id, label),
  EXCLUDE USING gist (institution_id WITH =, effective WITH &&)   -- catalogs never overlap
);

CREATE TABLE term (
  term_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint    NOT NULL REFERENCES institution,
  catalog_year_id bigint    NOT NULL REFERENCES catalog_year,
  code            text      NOT NULL,            -- registrar code, e.g. '202610'
  name            text      NOT NULL,            -- 'Fall 2026'
  season          text      NOT NULL CHECK (season IN ('fall','winter','spring','summer')),
  year            smallint  NOT NULL,
  dates           daterange NOT NULL,            -- first class day .. last day of finals
  UNIQUE (institution_id, code)
);

-- Sub-periods within a term: full term, first half, second half, summer session A/B, ...
-- Half-semester courses attach to one of these; their meeting blocks inherit its dates.
CREATE TABLE term_part (
  term_part_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  term_id         bigint    NOT NULL REFERENCES term ON DELETE CASCADE,
  code            text      NOT NULL,            -- '1', 'H1', 'A'
  name            text      NOT NULL,            -- 'Second eight weeks'
  dates           daterange NOT NULL,
  UNIQUE (term_id, code)
);

-- =============================================================================
-- 2. Catalog: courses, codes over time, attributes
-- =============================================================================

-- Stable identity of a course across renumberings and cross-listings. It deliberately
-- carries no code, title or credits: those live on course_catalog_entry, per catalog year.
CREATE TABLE course (
  course_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint      NOT NULL REFERENCES institution,
  created_at      timestamptz NOT NULL DEFAULT now(),
  notes           text                            -- curator notes, e.g. 'was MATH 220 until 2022'
);

-- Gen-ed / attribute tags ('WI' writing intensive, 'QR', 'DIV', honors...).
CREATE TABLE course_attribute (
  attribute_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint NOT NULL REFERENCES institution,
  code            text   NOT NULL,
  name            text   NOT NULL,
  UNIQUE (institution_id, code)
);

-- One row per (course, catalog year, code).
--   Renumbering  : same course_id, a different code in a later catalog year.
--   Cross-listing: same course_id, two rows in the SAME catalog year; is_primary marks the
--                  owning department's code.
-- "What was CS 401 in 2022?" is a lookup on (catalog_year_id, subject_code, course_number).
CREATE TABLE course_catalog_entry (
  entry_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  course_id       bigint       NOT NULL REFERENCES course,
  catalog_year_id bigint       NOT NULL REFERENCES catalog_year,
  subject_code    text         NOT NULL,          -- 'MATH'
  course_number   text         NOT NULL,          -- '220', '2200', '101L', '499H' — text on purpose
  level           smallint,                       -- 100, 200, 300 ... or 1000, 2000; stored, not parsed
  title           text         NOT NULL,
  description     text,
  credits_min     numeric(4,2) NOT NULL,
  credits_max     numeric(4,2) NOT NULL,
  is_primary      boolean      NOT NULL DEFAULT true,
  UNIQUE (catalog_year_id, subject_code, course_number),
  CHECK (credits_max >= credits_min)
);
CREATE INDEX course_catalog_entry_course     ON course_catalog_entry (course_id, catalog_year_id);
CREATE INDEX course_catalog_entry_code       ON course_catalog_entry (subject_code, course_number);
CREATE UNIQUE INDEX course_catalog_entry_one_primary
  ON course_catalog_entry (course_id, catalog_year_id) WHERE is_primary;

CREATE TABLE course_catalog_entry_attribute (
  entry_id        bigint NOT NULL REFERENCES course_catalog_entry ON DELETE CASCADE,
  attribute_id    bigint NOT NULL REFERENCES course_attribute,
  PRIMARY KEY (entry_id, attribute_id)
);
CREATE INDEX course_catalog_entry_attribute_attr ON course_catalog_entry_attribute (attribute_id);

-- Reusable named lists of courses ("Group A", "Approved statistics electives").
-- Referenced by requirement leaves; catalog_year_id NULL means the list is not year-specific.
CREATE TABLE course_list (
  course_list_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint NOT NULL REFERENCES institution,
  catalog_year_id bigint REFERENCES catalog_year,
  name            text   NOT NULL,
  UNIQUE NULLS NOT DISTINCT (institution_id, catalog_year_id, name)
);

CREATE TABLE course_list_member (
  course_list_id  bigint NOT NULL REFERENCES course_list ON DELETE CASCADE,
  course_id       bigint NOT NULL REFERENCES course,
  PRIMARY KEY (course_list_id, course_id)
);
CREATE INDEX course_list_member_course ON course_list_member (course_id);

-- =============================================================================
-- 3. People and places
-- =============================================================================

CREATE TABLE instructor (
  instructor_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint NOT NULL REFERENCES institution,
  external_id     text,                           -- registrar / HR id when the feed has one
  display_name    text   NOT NULL,
  family_name     text,
  given_name      text,
  email           text,
  -- Canonical form for matching: lower-cased, diacritics stripped, punctuation removed,
  -- 'family given'. Computed by the ingest layer (unaccent() is not IMMUTABLE, so it can't
  -- be a generated column).
  name_normalized text   NOT NULL,
  UNIQUE NULLS NOT DISTINCT (institution_id, external_id)
);
CREATE INDEX instructor_name_trgm ON instructor USING gin (name_normalized gin_trgm_ops);

-- Every spelling we've ever seen for an instructor. Grade feeds write here when a human or
-- a matcher links a raw name to a record, so the next import resolves it exactly.
CREATE TABLE instructor_name_alias (
  alias_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instructor_id   bigint      NOT NULL REFERENCES instructor ON DELETE CASCADE,
  name_normalized text        NOT NULL,
  source          text        NOT NULL CHECK (source IN ('registrar','grade_feed','manual')),
  confidence      real        CHECK (confidence BETWEEN 0 AND 1),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (instructor_id, name_normalized)
);
-- Not unique across instructors on purpose: 'smith j' may legitimately map to two people.
CREATE INDEX instructor_name_alias_name ON instructor_name_alias (name_normalized);

CREATE TABLE location (
  location_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint NOT NULL REFERENCES institution,
  campus          text,
  building_code   text,
  building_name   text,
  room            text,
  UNIQUE NULLS NOT DISTINCT (institution_id, campus, building_code, room)
);

-- =============================================================================
-- 4. Offerings: sections, instructors, meetings
-- =============================================================================

-- Sections that are the same class under different codes (CS 401 / MATH 401 this term).
-- They share meetings, instructor and usually a combined cap; a student can hold only one.
CREATE TABLE crosslist_group (
  crosslist_group_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  term_id            bigint NOT NULL REFERENCES term ON DELETE CASCADE,
  registrar_code     text,                        -- e.g. Banner XLST group id
  combined_capacity  int,
  combined_enrolled  int,
  UNIQUE NULLS NOT DISTINCT (term_id, registrar_code)
);

CREATE TABLE section (
  section_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  term_id             bigint NOT NULL REFERENCES term,
  term_part_id        bigint REFERENCES term_part,       -- NULL = full term
  course_id           bigint NOT NULL REFERENCES course,
  crn                 text   NOT NULL,
  section_code        text   NOT NULL,                   -- '001', 'A1', 'L02'
  -- Snapshot of the code exactly as published this term. Lets us display and match feeds
  -- without joining through catalog_year, and survives later renumberings.
  offered_subject_code   text NOT NULL,
  offered_course_number  text NOT NULL,
  component           text   NOT NULL CHECK (component IN
                        ('lecture','lab','discussion','seminar','studio','independent','other')),
  modality            text   NOT NULL CHECK (modality IN
                        ('in_person','online_sync','online_async','hybrid')),
  credits_min         numeric(4,2),                      -- NULL = as catalog
  credits_max         numeric(4,2),
  -- Current enrollment numbers. Denormalized: history lives in section_enrollment_snapshot.
  capacity            int,
  enrolled            int,
  waitlist_capacity   int,
  waitlist_count      int,
  status              text   NOT NULL DEFAULT 'open' CHECK (status IN
                        ('open','closed','waitlist','cancelled')),
  crosslist_group_id  bigint REFERENCES crosslist_group,
  first_seen_at       timestamptz NOT NULL DEFAULT now(),
  last_seen_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (term_id, crn)
);
CREATE INDEX section_term_course ON section (term_id, course_id);
CREATE INDEX section_term_code   ON section (term_id, offered_subject_code, offered_course_number);
CREATE INDEX section_crosslist   ON section (crosslist_group_id) WHERE crosslist_group_id IS NOT NULL;

-- Lecture + required lab/discussion. Registering for parent requires one child per link_group.
CREATE TABLE section_link (
  parent_section_id bigint NOT NULL REFERENCES section ON DELETE CASCADE,
  child_section_id  bigint NOT NULL REFERENCES section ON DELETE CASCADE,
  link_group        text   NOT NULL DEFAULT 'default',
  PRIMARY KEY (parent_section_id, child_section_id),
  CHECK (parent_section_id <> child_section_id)
);
CREATE INDEX section_link_child ON section_link (child_section_id);

-- Append-only history of the counters on section, one row per ingest observation.
CREATE TABLE section_enrollment_snapshot (
  section_id      bigint      NOT NULL REFERENCES section ON DELETE CASCADE,
  observed_at     timestamptz NOT NULL,
  capacity        int,
  enrolled        int,
  waitlist_count  int,
  status          text,
  PRIMARY KEY (section_id, observed_at)
);

-- Temporal assignment. "Instructor changed after publication" = close the old row's range
-- and open a new one; nothing is overwritten, and grade rows keep pointing at whoever
-- actually taught.
CREATE TABLE section_instructor (
  section_instructor_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  section_id      bigint    NOT NULL REFERENCES section ON DELETE CASCADE,
  instructor_id   bigint    NOT NULL REFERENCES instructor,
  role            text      NOT NULL DEFAULT 'primary' CHECK (role IN ('primary','secondary','ta')),
  effective       tstzrange NOT NULL DEFAULT tstzrange(now(), NULL, '[)'),
  source          text,
  EXCLUDE USING gist (section_id WITH =, instructor_id WITH =, role WITH =, effective WITH &&)
);
CREATE INDEX section_instructor_current       ON section_instructor (section_id) WHERE upper_inf(effective);
CREATE INDEX section_instructor_by_instructor ON section_instructor (instructor_id, section_id);

-- A meeting pattern as published: "MWF 10:00-10:50 in ENGR 101, Aug 24 - Dec 11".
-- One section may have several (lecture + lab, hybrid = one in-person block + one async block,
-- a single-date exam, a half-term block). This is the human-facing row; conflict detection
-- runs on meeting_slot, which is derived from it.
CREATE TABLE meeting_block (
  meeting_block_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  section_id      bigint     NOT NULL REFERENCES section ON DELETE CASCADE,
  kind            text       NOT NULL DEFAULT 'class' CHECK (kind IN
                    ('class','lab','discussion','exam','other')),
  modality        text       NOT NULL CHECK (modality IN
                    ('in_person','online_sync','online_async')),
  days            smallint[],                     -- ISO weekdays 1=Mon..7=Sun. NULL if async/TBA,
                                                  -- or if dates is a single day (derived then).
  start_time      time,                           -- institution-local. NULL if async/TBA.
  end_time        time,
  dates           daterange  NOT NULL,            -- from term_part/term, or a single day
  time_tba        boolean    NOT NULL DEFAULT false,
  location_id     bigint     REFERENCES location,
  location_tba    boolean    NOT NULL DEFAULT false,
  location_text   text,                           -- raw string when it didn't match a location
  CHECK (days IS NULL OR (cardinality(days) > 0 AND days <@ ARRAY[1,2,3,4,5,6,7]::smallint[])),
  CHECK (
    modality = 'online_async' OR time_tba
    OR (start_time IS NOT NULL AND end_time > start_time
        AND (days IS NOT NULL OR upper(dates) - lower(dates) = 1))
  ),
  CHECK (NOT (modality = 'online_async' AND (days IS NOT NULL OR start_time IS NOT NULL)))
);
CREATE INDEX meeting_block_section ON meeting_block (section_id);

-- DERIVED from meeting_block. One row per (block, weekday). This is the unit of conflict
-- detection: two slots conflict iff same weekday, minutes overlap, dates overlap.
-- Async blocks and TBA times produce no slots, so they never conflict with anything.
CREATE TABLE meeting_slot (
  meeting_slot_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  meeting_block_id bigint    NOT NULL REFERENCES meeting_block ON DELETE CASCADE,
  section_id       bigint    NOT NULL REFERENCES section ON DELETE CASCADE,
  term_id          bigint    NOT NULL REFERENCES term,      -- copied from section for the index
  weekday          smallint  NOT NULL CHECK (weekday BETWEEN 1 AND 7),
  minutes          int4range NOT NULL,                      -- [start, end) minutes after midnight
  dates            daterange NOT NULL
);
CREATE INDEX meeting_slot_conflict ON meeting_slot USING gist (term_id, weekday, minutes, dates);
CREATE INDEX meeting_slot_section  ON meeting_slot (section_id);

CREATE FUNCTION meeting_block_sync_slots() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  day_list smallint[];
BEGIN
  DELETE FROM meeting_slot WHERE meeting_block_id = NEW.meeting_block_id;

  IF NEW.modality = 'online_async' OR NEW.time_tba OR NEW.start_time IS NULL THEN
    RETURN NEW;
  END IF;

  day_list := COALESCE(
    NEW.days,
    ARRAY[EXTRACT(isodow FROM lower(NEW.dates))::smallint]   -- single-date meeting
  );

  INSERT INTO meeting_slot (meeting_block_id, section_id, term_id, weekday, minutes, dates)
  SELECT NEW.meeting_block_id, NEW.section_id, s.term_id, d,
         int4range(minute_of_day(NEW.start_time), minute_of_day(NEW.end_time), '[)'),
         NEW.dates
  FROM section s, unnest(day_list) AS d
  WHERE s.section_id = NEW.section_id;

  RETURN NEW;
END $$;

CREATE TRIGGER meeting_block_sync
  AFTER INSERT OR UPDATE ON meeting_block
  FOR EACH ROW EXECUTE FUNCTION meeting_block_sync_slots();

-- =============================================================================
-- 5. Grade distributions
-- =============================================================================

-- Provenance for every ingest run (registrar pulls, FOIA grade spreadsheets, manual fixes).
CREATE TABLE import_batch (
  import_batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint      NOT NULL REFERENCES institution,
  source          text        NOT NULL,           -- 'registrar_api', 'grades_fall2025.xlsx'
  fetched_at      timestamptz NOT NULL DEFAULT now(),
  notes           text
);

-- Institution's grading scheme: which symbols exist, their GPA points, which are withdrawals.
CREATE TABLE grade_scale (
  institution_id  bigint       NOT NULL REFERENCES institution,
  grade_symbol    text         NOT NULL,          -- 'A','A-','B+',...,'F','P','NP','S','U','I','W'
  points          numeric(3,2),                   -- NULL = excluded from GPA
  is_withdrawal   boolean      NOT NULL DEFAULT false,
  sort_order      smallint     NOT NULL,
  PRIMARY KEY (institution_id, grade_symbol)
);

-- One row per grade-report line as received. Raw keys are kept verbatim and never
-- overwritten; resolved FKs are filled in by matching, and the *_match columns record how.
CREATE TABLE grade_distribution (
  grade_distribution_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id      bigint NOT NULL REFERENCES institution,
  import_batch_id     bigint NOT NULL REFERENCES import_batch,
  term_id             bigint NOT NULL REFERENCES term,
  -- raw keys, exactly as the feed spelled them
  raw_subject_code    text   NOT NULL,
  raw_course_number   text   NOT NULL,
  raw_section_code    text,
  raw_crn             text,
  raw_instructor_name text,
  -- resolved links
  section_id          bigint REFERENCES section,
  course_id           bigint REFERENCES course,
  instructor_id       bigint REFERENCES instructor,
  section_match       text   NOT NULL DEFAULT 'unmatched' CHECK (section_match IN
                        ('unmatched','crn','code_and_section','course_only','manual')),
  instructor_match    text   NOT NULL DEFAULT 'unmatched' CHECK (instructor_match IN
                        ('unmatched','exact','alias','fuzzy','manual','ambiguous')),
  -- headline numbers (denormalized from grade_distribution_bucket for sort/filter)
  total_count         int    NOT NULL CHECK (total_count >= 0),
  graded_count        int    CHECK (graded_count >= 0),  -- GPA denominator (excludes W/I/P/...)
  w_count             int    NOT NULL DEFAULT 0 CHECK (w_count >= 0),
  gpa_mean            numeric(4,3),
  gpa_source          text   CHECK (gpa_source IN ('supplied','computed')),
  UNIQUE NULLS NOT DISTINCT (import_batch_id, raw_subject_code, raw_course_number,
                             raw_section_code, raw_crn, raw_instructor_name)
);
CREATE INDEX grade_distribution_section      ON grade_distribution (section_id);
CREATE INDEX grade_distribution_course_instr ON grade_distribution (course_id, instructor_id, term_id);
CREATE INDEX grade_distribution_unresolved   ON grade_distribution (institution_id, import_batch_id)
  WHERE section_match = 'unmatched' OR instructor_match IN ('unmatched','ambiguous');

CREATE TABLE grade_distribution_bucket (
  grade_distribution_id bigint NOT NULL REFERENCES grade_distribution ON DELETE CASCADE,
  grade_symbol    text NOT NULL,                  -- should exist in grade_scale for the institution
  count           int  NOT NULL CHECK (count >= 0),
  PRIMARY KEY (grade_distribution_id, grade_symbol)
);

-- Roll-up for "how does this instructor grade this course" cards. Refresh after each grade import.
CREATE MATERIALIZED VIEW course_instructor_grade_summary AS
SELECT course_id,
       instructor_id,
       count(*)                                                  AS n_offerings,
       sum(total_count)                                          AS n_students,
       sum(gpa_mean * graded_count) / nullif(sum(graded_count), 0) AS gpa_mean_weighted,
       sum(w_count)::numeric / nullif(sum(total_count), 0)       AS w_rate,
       max(term_id)                                              AS latest_term_id
FROM grade_distribution
WHERE course_id IS NOT NULL
GROUP BY course_id, instructor_id;
CREATE UNIQUE INDEX course_instructor_grade_summary_pk
  ON course_instructor_grade_summary (course_id, instructor_id) NULLS NOT DISTINCT;

-- =============================================================================
-- 6. Programs and the requirement tree
-- =============================================================================

CREATE TABLE program (
  program_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint NOT NULL REFERENCES institution,
  code            text   NOT NULL,                -- 'BS-CS'
  name            text   NOT NULL,
  kind            text   NOT NULL CHECK (kind IN
                    ('degree','major','minor','concentration','certificate','gen_ed')),
  UNIQUE (institution_id, code)
);

-- A program's rules as they stood in one catalog year. The whole requirement tree hangs
-- off this row; students bind to a (program, catalog_year) pair, never to "the program".
CREATE TABLE program_version (
  program_version_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  program_id      bigint       NOT NULL REFERENCES program,
  catalog_year_id bigint       NOT NULL REFERENCES catalog_year,
  total_credits   numeric(5,2),
  min_gpa         numeric(3,2),
  UNIQUE (program_id, catalog_year_id)
);

-- INTERNAL NODE of the requirement tree.
--   all     : every child must be satisfied
--   any_n   : at least min_count children satisfied  (min_count = 1 is "either/or")
--   credits : at least min_credits earned across satisfied children
-- Children are requirement_group rows (parent_group_id) and requirement rows (group_id),
-- ordered together by position. Exactly one root per program_version (parent NULL).
CREATE TABLE requirement_group (
  group_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  program_version_id bigint NOT NULL REFERENCES program_version ON DELETE CASCADE,
  parent_group_id bigint       REFERENCES requirement_group ON DELETE CASCADE,
  position        smallint     NOT NULL DEFAULT 0,
  title           text,                           -- 'Core', 'Upper-division electives'
  description     text,
  operator        text         NOT NULL CHECK (operator IN ('all','any_n','credits')),
  min_count       smallint,
  min_credits     numeric(5,2),
  CHECK (operator <> 'any_n'   OR min_count   IS NOT NULL),
  CHECK (operator <> 'credits' OR min_credits IS NOT NULL)
);
CREATE INDEX requirement_group_version ON requirement_group (program_version_id);
CREATE INDEX requirement_group_parent  ON requirement_group (parent_group_id, position);
CREATE UNIQUE INDEX requirement_group_one_root
  ON requirement_group (program_version_id) WHERE parent_group_id IS NULL;

-- LEAF of the requirement tree. Defines a candidate set of courses and how much of it is needed.
-- Candidate set = (course_id, if set) ∪ (members of course_list_id, if set),
--                 or every course if neither is set,
--                 then narrowed by subject_codes / min_level / max_level / attribute_id when set.
-- Satisfied when the student has >= min_count courses (and >= min_credits, if set) from that
-- set at or above min_grade_symbol.
--   "MATH 220"                                    -> course_id
--   "3 of the following 7"                        -> course_list_id + min_count 3
--   "one from group A at 300 level or above"      -> course_list_id + min_level 300
--   "9 credits of ECON at 400 level"              -> subject_codes {ECON} + min_level 400 + min_credits 9
--   "either MATH 220 or both MATH 210 and 211"    -> group any_n(1): [leaf 220, group all: [leaf 210, leaf 211]]
CREATE TABLE requirement (
  requirement_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  program_version_id bigint NOT NULL REFERENCES program_version ON DELETE CASCADE,
  group_id        bigint       NOT NULL REFERENCES requirement_group ON DELETE CASCADE,
  position        smallint     NOT NULL DEFAULT 0,
  title           text,
  course_id       bigint       REFERENCES course,
  course_list_id  bigint       REFERENCES course_list,
  subject_codes   text[],
  min_level       smallint,
  max_level       smallint,
  attribute_id    bigint       REFERENCES course_attribute,
  min_count       smallint     NOT NULL DEFAULT 1 CHECK (min_count >= 1),
  min_credits     numeric(5,2),
  min_grade_symbol text,
  CHECK (course_id IS NULL OR course_list_id IS NULL),
  CHECK (max_level IS NULL OR min_level IS NULL OR max_level >= min_level)
);
CREATE INDEX requirement_version ON requirement (program_version_id);
CREATE INDEX requirement_by_group ON requirement (group_id, position);
CREATE INDEX requirement_course  ON requirement (course_id) WHERE course_id IS NOT NULL;
CREATE INDEX requirement_list    ON requirement (course_list_id) WHERE course_list_id IS NOT NULL;

-- =============================================================================
-- 7. Students, plans, constraints
-- =============================================================================

CREATE TABLE student (
  student_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  bigint      NOT NULL REFERENCES institution,
  external_id     text,                           -- SIS id; NULL for self-signup users
  matriculation_term_id bigint REFERENCES term,
  catalog_year_id bigint      NOT NULL REFERENCES catalog_year,  -- default binding (matriculation year)
  expected_graduation_term_id bigint REFERENCES term,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (institution_id, external_id)
);

-- Declared programs. catalog_year_id here is the binding that actually governs evaluation;
-- it defaults to student.catalog_year_id but can differ (late declaration, elected catalog
-- change). The composite FK guarantees a program_version exists for that pair.
CREATE TABLE student_program (
  student_id      bigint  NOT NULL REFERENCES student ON DELETE CASCADE,
  program_id      bigint  NOT NULL REFERENCES program,
  catalog_year_id bigint  NOT NULL REFERENCES catalog_year,
  declared_on     date,
  is_primary      boolean NOT NULL DEFAULT false,
  PRIMARY KEY (student_id, program_id),
  FOREIGN KEY (program_id, catalog_year_id) REFERENCES program_version (program_id, catalog_year_id)
);

CREATE TABLE student_plan (
  plan_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  student_id      bigint      NOT NULL REFERENCES student ON DELETE CASCADE,
  name            text        NOT NULL DEFAULT 'My plan',
  is_primary      boolean     NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX student_plan_one_primary ON student_plan (student_id) WHERE is_primary;

-- One line of a plan: a course in a term, optionally pinned to a section, or a placeholder
-- for "something that satisfies requirement X". Completed/transcript rows live here too,
-- distinguished by status + source.
CREATE TABLE plan_item (
  plan_item_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  plan_id         bigint       NOT NULL REFERENCES student_plan ON DELETE CASCADE,
  term_id         bigint       REFERENCES term,           -- NULL = not yet placed in a term
  course_id       bigint       REFERENCES course,
  section_id      bigint       REFERENCES section,        -- when chosen; must agree with course_id/term_id
  requirement_id  bigint       REFERENCES requirement,    -- placeholder target
  status          text         NOT NULL DEFAULT 'planned' CHECK (status IN
                    ('planned','registered','waitlisted','in_progress','completed','withdrawn','transfer')),
  credits         numeric(4,2),                           -- as taken/planned (variable-credit courses)
  grade_symbol    text,
  position        smallint     NOT NULL DEFAULT 0,
  source          text         NOT NULL DEFAULT 'user' CHECK (source IN ('user','transcript','registration')),
  CHECK (course_id IS NOT NULL OR section_id IS NOT NULL OR requirement_id IS NOT NULL)
);
CREATE INDEX plan_item_plan_term ON plan_item (plan_id, term_id);
CREATE INDEX plan_item_section   ON plan_item (section_id) WHERE section_id IS NOT NULL;
CREATE INDEX plan_item_course    ON plan_item (course_id)  WHERE course_id  IS NOT NULL;

-- Keep plan_item.course_id / term_id consistent with the pinned section.
CREATE FUNCTION plan_item_fill_from_section() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.section_id IS NOT NULL THEN
    SELECT s.course_id, s.term_id INTO NEW.course_id, NEW.term_id
    FROM section s WHERE s.section_id = NEW.section_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER plan_item_fill
  BEFORE INSERT OR UPDATE OF section_id ON plan_item
  FOR EACH ROW EXECUTE FUNCTION plan_item_fill_from_section();

-- Scheduling preferences. Typed columns per kind; is_hard=false rows are scored, not enforced.
CREATE TABLE student_constraint (
  constraint_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  student_id      bigint      NOT NULL REFERENCES student ON DELETE CASCADE,
  plan_id         bigint      REFERENCES student_plan ON DELETE CASCADE,  -- NULL = all plans
  term_id         bigint      REFERENCES term,                            -- NULL = every term
  kind            text        NOT NULL CHECK (kind IN (
                    'blocked_time',      -- days + start_time + end_time (+ dates)
                    'earliest_start',    -- start_time  (no class before this)
                    'latest_end',        -- end_time    (no class after this)
                    'no_days',           -- days
                    'max_credits',       -- int_value
                    'min_credits',       -- int_value
                    'avoid_instructor',  -- instructor_id
                    'prefer_instructor', -- instructor_id
                    'campus',            -- text_value
                    'max_back_to_back_minutes')),  -- int_value
  is_hard         boolean     NOT NULL DEFAULT true,
  weight          real,                           -- soft constraints only
  label           text,                           -- 'work', 'practice'
  days            smallint[],
  start_time      time,
  end_time        time,
  dates           daterange,                      -- NULL = whole term
  int_value       int,
  instructor_id   bigint      REFERENCES instructor,
  text_value      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (days IS NULL OR (cardinality(days) > 0 AND days <@ ARRAY[1,2,3,4,5,6,7]::smallint[])),
  CHECK (kind <> 'blocked_time'   OR (start_time IS NOT NULL AND end_time > start_time)),
  CHECK (kind <> 'earliest_start' OR start_time IS NOT NULL),
  CHECK (kind <> 'latest_end'     OR end_time   IS NOT NULL),
  CHECK (kind <> 'no_days'        OR days       IS NOT NULL),
  CHECK (kind NOT IN ('max_credits','min_credits','max_back_to_back_minutes') OR int_value IS NOT NULL),
  CHECK (kind NOT IN ('avoid_instructor','prefer_instructor') OR instructor_id IS NOT NULL),
  CHECK (kind <> 'campus'         OR text_value IS NOT NULL)
);
CREATE INDEX student_constraint_student ON student_constraint (student_id, term_id);

-- DERIVED from student_constraint. Time-shaped constraints flattened into the same shape as
-- meeting_slot, so "does this section fit my life" is the same query as "does it clash with
-- my other classes".
CREATE TABLE student_constraint_slot (
  constraint_slot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  constraint_id   bigint    NOT NULL REFERENCES student_constraint ON DELETE CASCADE,
  student_id      bigint    NOT NULL REFERENCES student ON DELETE CASCADE,
  plan_id         bigint    REFERENCES student_plan ON DELETE CASCADE,
  term_id         bigint    REFERENCES term,
  is_hard         boolean   NOT NULL,
  weekday         smallint  NOT NULL CHECK (weekday BETWEEN 1 AND 7),
  minutes         int4range NOT NULL,
  dates           daterange NOT NULL
);
CREATE INDEX student_constraint_slot_student ON student_constraint_slot (student_id, weekday);

CREATE FUNCTION student_constraint_sync_slots() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  day_list smallint[] := COALESCE(NEW.days, ARRAY[1,2,3,4,5,6,7]::smallint[]);
  span     daterange  := COALESCE(NEW.dates, '(,)'::daterange);
  win      int4range;
BEGIN
  DELETE FROM student_constraint_slot WHERE constraint_id = NEW.constraint_id;

  win := CASE NEW.kind
           WHEN 'blocked_time'   THEN int4range(minute_of_day(NEW.start_time), minute_of_day(NEW.end_time), '[)')
           WHEN 'earliest_start' THEN int4range(0, minute_of_day(NEW.start_time), '[)')
           WHEN 'latest_end'     THEN int4range(minute_of_day(NEW.end_time), 1440, '[)')
           WHEN 'no_days'        THEN int4range(0, 1440, '[)')
           ELSE NULL
         END;

  IF win IS NOT NULL AND NOT isempty(win) THEN
    INSERT INTO student_constraint_slot
      (constraint_id, student_id, plan_id, term_id, is_hard, weekday, minutes, dates)
    SELECT NEW.constraint_id, NEW.student_id, NEW.plan_id, NEW.term_id, NEW.is_hard, d, win, span
    FROM unnest(day_list) AS d;
  END IF;

  RETURN NEW;
END $$;

CREATE TRIGGER student_constraint_sync
  AFTER INSERT OR UPDATE ON student_constraint
  FOR EACH ROW EXECUTE FUNCTION student_constraint_sync_slots();

-- =============================================================================
-- 8. The queries the indexes exist for (reference; not executed)
-- =============================================================================
-- (a) Pairwise conflicts inside a plan for one term:
--   SELECT a.section_id, b.section_id
--   FROM meeting_slot a
--   JOIN meeting_slot b ON b.term_id = a.term_id AND b.weekday = a.weekday
--                      AND b.minutes && a.minutes AND b.dates && a.dates
--                      AND b.section_id > a.section_id
--   WHERE a.section_id = ANY($plan_sections) AND b.section_id = ANY($plan_sections);
--
-- (b) Every section in a term that fits around the plan and the student's hard constraints:
--   WITH busy AS (
--     SELECT ms.weekday, ms.minutes, ms.dates
--     FROM meeting_slot ms JOIN plan_item pi ON pi.section_id = ms.section_id
--     WHERE pi.plan_id = $plan AND ms.term_id = $term
--     UNION ALL
--     SELECT weekday, minutes, dates FROM student_constraint_slot
--     WHERE student_id = $student AND is_hard
--       AND (term_id IS NULL OR term_id = $term) AND (plan_id IS NULL OR plan_id = $plan)
--   )
--   SELECT s.*
--   FROM section s
--   WHERE s.term_id = $term
--     AND NOT EXISTS (SELECT 1 FROM meeting_slot ms JOIN busy
--                       ON busy.weekday = ms.weekday AND busy.minutes && ms.minutes AND busy.dates && ms.dates
--                     WHERE ms.section_id = s.section_id)
--     AND NOT EXISTS (SELECT 1 FROM section mine JOIN plan_item pi ON pi.section_id = mine.section_id
--                     WHERE pi.plan_id = $plan AND mine.crosslist_group_id = s.crosslist_group_id);
--
-- (c) Fetch a student's whole requirement tree in two queries, no recursion:
--   SELECT * FROM requirement_group WHERE program_version_id = $pv ORDER BY parent_group_id, position;
--   SELECT * FROM requirement       WHERE program_version_id = $pv ORDER BY group_id, position;
--   where $pv comes from student_program (program_id, catalog_year_id) -> program_version.
--
-- (d) Who is teaching this section right now:
--   SELECT * FROM section_instructor WHERE section_id = $s AND upper_inf(effective);
--
-- (e) Resolve a raw grade-feed instructor name:
--   1. exact:  instructor_name_alias.name_normalized = $n
--   2. fuzzy:  SELECT * FROM instructor WHERE institution_id = $i
--              ORDER BY name_normalized <-> $n LIMIT 5;   -- pg_trgm, GIN-indexed
