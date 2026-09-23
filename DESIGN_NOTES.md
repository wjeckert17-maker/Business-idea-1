# Course-planning data model: design notes

Companion to `schema.sql` (PostgreSQL 15+; needs `btree_gist` and `pg_trgm`).

## 1. How the four hard problems are handled

### Messy meeting patterns and fast conflict detection

Two tables, one human-facing and one machine-facing.

- `meeting_block` is the pattern as published: days array, start/end time, a `daterange`, location, modality, kind. It represents every case you listed:
  - MWF 50-min / TR 75-min: `days = {1,3,5}` or `{2,4}`, times, full-term dates.
  - Lab that meets once: `dates` is a single day; `days` may be NULL and is derived from the date.
  - Half-semester: `dates` copied from the matching `term_part`.
  - Asynchronous online: `modality = 'online_async'`, no days/times. A hybrid section gets two blocks.
  - TBA room: `location_id NULL`, `location_tba = true`, raw string kept in `location_text`.
  - TBA time: `time_tba = true`, days/times NULL.
- `meeting_slot` is DERIVED by trigger: one row per (block, weekday) with `minutes int4range` and `dates daterange`. Two slots conflict iff same weekday AND minutes overlap AND dates overlap. Async and TBA blocks produce no slots, so they can never conflict, which is the correct behaviour.

Why explode to one row per weekday instead of keeping a bitmask on the block: with a weekday column, the whole predicate (`term_id =, weekday =, minutes &&, dates &&`) is served by a single GiST index, and the same row shape can be reused for student time constraints (below). A bitmask would need a post-filter after the range lookup.

### Recursive requirements

`requirement_group` is the internal node (operators `all`, `any_n`, `credits`), `requirement` is the leaf. A leaf defines a candidate course set (a single course, a reusable `course_list`, and/or a filter on subject, level, attribute) plus a threshold (`min_count`, `min_credits`, `min_grade_symbol`). Your three examples:

| Rule | Encoding |
|---|---|
| 3 of the following 7 | one leaf: `course_list_id` = the 7, `min_count = 3` |
| one from group A at the 300 level or above | one leaf: `course_list_id` = A, `min_level = 300`, `min_count = 1` |
| either MATH 220 or both MATH 210 and 211 | group `any_n(1)` with children [leaf MATH 220, group `all` with [leaf 210, leaf 211]] |

Leaves reference `course_id` (stable identity), never a code, so renumbering does not break old catalogs.

### Renumbering, cross-listing, instructor changes, name matching

- `course` is a code-less identity. `course_catalog_entry` holds (code, title, credits) per catalog year. Renumbering = same `course_id`, new code in a later year. Cross-listing = same `course_id`, two entries in the same year, one flagged `is_primary`.
- `section` snapshots the code it was published under (`offered_subject_code`, `offered_course_number`) so history and feed matching survive later renumbering. Section-level cross-lists share a `crosslist_group`.
- `section_instructor` is temporal (`effective tstzrange` with an exclusion constraint). An instructor change closes one range and opens another. Nothing is overwritten, and a grade row matched to the person who actually taught stays correct.
- `grade_distribution` keeps every raw key verbatim (`raw_instructor_name`, `raw_crn`, ...) beside nullable resolved FKs, with `section_match` / `instructor_match` recording how each link was made. `instructor_name_alias` accumulates every spelling ever linked, so a name resolves exactly the second time. `pg_trgm` on `instructor.name_normalized` handles the first time.

### Catalog-year binding

`catalog_year` is a first-class row with a non-overlapping `daterange`. `program_version` is unique per (program, catalog year) and owns the whole requirement tree. `student.catalog_year_id` is the default binding; `student_program.catalog_year_id` is the one that governs evaluation, and a composite FK guarantees a `program_version` exists for that pair. Terms carry `catalog_year_id` too, so "which catalog was in force when this section ran" is a direct join.

## 2. Denormalizations (each one is for a specific query)

| Where | What is duplicated | Query it serves | Kept in sync by |
|---|---|---|---|
| `meeting_slot` | expansion of `meeting_block` per weekday | all conflict checks, "sections that fit my schedule" | trigger `meeting_block_sync` + cascade delete |
| `meeting_slot.term_id` | copied from `section` | leading column of the conflict GiST index, so a term's slots are physically clustered | trigger |
| `student_constraint_slot` | time-shaped constraints flattened to slot shape | one query checks both class clashes and life constraints | trigger `student_constraint_sync` |
| `section.capacity / enrolled / waitlist_*` | latest values from `section_enrollment_snapshot` | section list pages ("open seats") without a window function per row | ingest writes both |
| `section.offered_subject_code / number` | code from the catalog entry of that term's catalog year | display and feed matching without a 3-way join; immune to later renumbering | ingest |
| `requirement.program_version_id`, `requirement_group.program_version_id` | derivable by walking to the root | fetch an entire tree with two indexed lookups instead of a recursive CTE | ingest; trees are written once per catalog year |
| `grade_distribution.total_count / w_count / gpa_mean` | aggregates of `grade_distribution_bucket` | sort and filter sections by GPA or W-rate | ingest |
| `course_instructor_grade_summary` (matview) | per (course, instructor) roll-up across terms | "how does this instructor grade this course" cards | `REFRESH MATERIALIZED VIEW CONCURRENTLY` after grade imports |
| `plan_item.course_id / term_id` when `section_id` set | from `section` | plan views that don't need a section join; placeholder rows have no section | trigger `plan_item_fill` |

## 3. Indexes

| Index | Query |
|---|---|
| `meeting_slot_conflict` GiST (term_id, weekday, minutes, dates) | every `&&` overlap probe; the core of conflict detection |
| `meeting_slot_section` | delete/rebuild slots for one section; pull a section's slots for the plan |
| `section (term_id, crn)` unique | feed upserts keyed by CRN |
| `section_term_course` | "sections of course X this term" |
| `section_term_code` | resolve grade rows and search by published code |
| `section_crosslist` partial | "can't take both" check across a cross-list group |
| `section_instructor_current` partial on `upper_inf(effective)` | who teaches it now, without scanning history |
| `section_instructor_by_instructor` | instructor page: everything they have taught |
| `course_catalog_entry (catalog_year_id, subject, number)` unique | "what is MATH 220 in catalog 2022" |
| `course_catalog_entry_code` | code lookup across years (renumbering history, autocomplete) |
| `course_catalog_entry_one_primary` partial unique | at most one primary listing per course per year |
| `instructor_name_trgm` GIN | fuzzy name matching on grade import |
| `instructor_name_alias_name` | exact alias hit before falling back to fuzzy |
| `grade_distribution_unresolved` partial | the curation queue of rows a human must link |
| `grade_distribution_course_instr` | matview refresh and instructor/course drill-downs |
| `requirement_group_one_root` partial unique | exactly one root per program version |
| `requirement_*` on program_version_id and (group_id, position) | tree fetch in order |
| `student_plan_one_primary` partial unique | one primary plan per student |
| `plan_item_plan_term` | render a plan term by term |
| `plan_item_section` partial | "how many students have planned this section" and cascade checks |
| Exclusion constraints on `catalog_year.effective` and `section_instructor.effective` | data integrity, not speed: no overlapping catalog years, no double-assigned instructor ranges |

Deliberately not added: a room double-booking exclusion on `meeting_slot`. Cross-listed sections legitimately share a room and time, so it would reject real registrar data.

## 4. Underspecified points (I chose a default; confirm or change)

1. **Cross-listing semantics.** I modeled a cross-list as one `course_id` with several codes, so a requirement naming MATH 401 is satisfied by CS 401. Some institutions pair grad/undergrad numbers (CS 401 / CS 501) with different credit or workload; those should be distinct courses with an equivalence table that does not exist yet.
2. **Whether every course gets a catalog entry every year.** The schema assumes the ingest writes one `course_catalog_entry` per active course per catalog year. If your source only emits rows on change, lookups need "latest entry at or before year Y" logic.
3. **Linking renumbered courses.** Nothing in a registrar feed says "MATH 2200 is the old MATH 220" reliably. `course.notes` and the multi-year entry table hold the result, but the linking is a curation step you haven't specified.
4. **Grade data granularity.** I allowed rows at CRN level, at (code, section) level, and at course-only level via `section_match`. If your source aggregates across sections per instructor, `section_id` will usually be NULL and the matview is the useful surface. Also unknown: whether GPA mean is supplied or must be computed from buckets (`gpa_source` records which), and the exact grade-symbol set (`grade_scale` is per institution; buckets are not FK-checked against it).
5. **Requirement semantics not in your list.** Double counting (can one course satisfy two leaves?), exclusions ("not both CS 101 and CS 105"), residency and minimum-GPA rules, and whether "300 level or above" is a number range or a `level` field. I store `level` explicitly and left the others out rather than guess. The canonical form is also open: "3 of 7" can be one list-leaf or an `any_n(3)` group of 7 course-leaves. I recommend list-leaf when children are plain courses, groups only when children are compound, and enforcing that in the ingest.
6. **Catalog year: per student or per program?** I gave both (`student.catalog_year_id` default, `student_program.catalog_year_id` governing). If students can never diverge per program, drop the latter.
7. **Meeting edge cases.** Final exam schedules, overlap tolerance (some schools allow a 10-minute overlap with permission), travel time between campuses, and meetings that do not recur weekly (every other week) are not represented. A biweekly pattern would need a `week_parity` or explicit date list.
8. **Section change history.** Only instructors and enrollment counters have history. If you need "the room changed on Aug 3", add an audit table on `section` / `meeting_block`.
9. **Plans.** Unknown whether a plan may hold two alternative sections of the same course in one term (allowed now), whether transcript rows belong in `plan_item` (they do now, via `source = 'transcript'`) or a separate table, and how transfer credit maps to `course_id`.
10. **Constraint scoring.** `is_hard` and `weight` exist, but no scoring function or unit for `weight` was specified.
11. **Term structure.** Quarters, trimesters and summer sessions all fit `term` + `term_part`, but the mapping of `term_part` codes from your registrar is unknown.
12. **Instructor identity.** If the registrar supplies no stable id, `instructor` rows will be created from names and will need the same alias curation as the grade feed.
13. **Multi-tenancy.** `institution_id` is on top-level tables only. If you want row-level security, it must be propagated to child tables (sections, slots, plan items).

## 5. Verification status

Parsed successfully with pglast (libpg_query, PostgreSQL 17 grammar), including the PL/pgSQL bodies. Not executed against a live server: no PostgreSQL was available on this machine. Things a live run would additionally check: operator-class availability for the GiST indexes (needs `btree_gist`, created at the top) and the `NULLS NOT DISTINCT` clauses (PostgreSQL 15+).
