"""Idempotent writes into the course-planning schema (schema.sql + schema_additions.sql).

Every method is safe to call repeatedly with the same data. Natural keys, not surrogate ids,
decide identity: (institution, code) for terms, (catalog_year, subject, number) for courses,
(term, crn) for sections, (institution, external_id) or normalized name for instructors.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import SchoolConfig
from .models import InstructorRecord, MeetingRecord, SectionRecord, TermInfo

log = logging.getLogger(__name__)

SEASON_NOMINAL_MONTH = {"fall": 9, "winter": 1, "spring": 2, "summer": 6}


def normalize_name(family: Optional[str], given: Optional[str], display: str) -> str:
    base = f"{family or ''} {given or ''}".strip() or display
    s = unicodedata.normalize("NFKD", base)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def course_level(number: str, style: str) -> Optional[int]:
    m = re.match(r"\d+", number)
    if not m:
        return None
    digits = m.group(0)
    if style == "thousands" or (style == "auto" and len(digits) >= 4):
        return int(digits[0]) * 1000
    if style == "hundreds" or (style == "auto" and len(digits) == 3):
        return int(digits[0]) * 100
    return None


def _dec(v) -> Optional[Decimal]:
    return None if v is None else Decimal(str(v)).quantize(Decimal("0.01"))


@dataclass
class SectionOutcome:
    section_id: int
    inserted: bool
    changed: bool           # any tracked column, instructor set, or meeting set differs
    counters_changed: bool  # capacity/enrolled/waitlist changed (drives snapshot rows)


class Loader:
    def __init__(self, conn, config: SchoolConfig):
        self.conn = conn
        self.config = config
        self.institution_id: Optional[int] = None
        self.reset_caches()

    def reset_caches(self) -> None:
        self._catalog_years: Dict[str, int] = {}
        self._term_parts: Dict[Tuple[int, str], int] = {}
        self._courses: Dict[Tuple[int, str, str], int] = {}
        self._instructors: Dict[str, int] = {}
        self._locations: Dict[Tuple, Optional[int]] = {}
        self._crosslists: Dict[Tuple[int, str], int] = {}

    # -- institution / batch ---------------------------------------------
    def ensure_institution(self) -> int:
        c = self.config
        row = self.conn.execute(
            """INSERT INTO institution (slug, name, timezone) VALUES (%s, %s, %s)
               ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, timezone = EXCLUDED.timezone
               RETURNING institution_id""",
            (c.slug, c.name, c.timezone),
        ).fetchone()
        self.institution_id = row[0]
        return row[0]

    def start_batch(self, source: str, fetched_at: datetime) -> int:
        return self.conn.execute(
            "INSERT INTO import_batch (institution_id, source, fetched_at) VALUES (%s, %s, %s) RETURNING import_batch_id",
            (self.institution_id, source, fetched_at),
        ).fetchone()[0]

    def finish_batch(self, batch_id: int, notes: str) -> None:
        self.conn.execute("UPDATE import_batch SET notes = %s WHERE import_batch_id = %s", (notes, batch_id))

    # -- calendar ---------------------------------------------------------
    def catalog_year_for(self, term: TermInfo) -> Tuple[str, date, date]:
        start_month = self.config.catalog_year_start_month
        nominal = SEASON_NOMINAL_MONTH.get(term.season, 9)
        ay = term.year if nominal >= start_month else term.year - 1
        return f"{ay}-{ay + 1}", date(ay, start_month, 1), date(ay + 1, start_month, 1)

    def ensure_catalog_year(self, term: TermInfo) -> int:
        label, lo, hi = self.catalog_year_for(term)
        if label in self._catalog_years:
            return self._catalog_years[label]
        self.conn.execute(
            """INSERT INTO catalog_year (institution_id, label, effective)
               VALUES (%s, %s, daterange(%s, %s, '[)'))
               ON CONFLICT (institution_id, label) DO NOTHING""",
            (self.institution_id, label, lo, hi),
        )
        row = self.conn.execute(
            "SELECT catalog_year_id FROM catalog_year WHERE institution_id = %s AND label = %s",
            (self.institution_id, label),
        ).fetchone()
        self._catalog_years[label] = row[0]
        return row[0]

    @staticmethod
    def default_term_dates(term: TermInfo) -> Tuple[date, date]:
        y = term.year
        return {
            "fall": (date(y, 8, 15), date(y, 12, 31)),
            "spring": (date(y, 1, 1), date(y, 5, 31)),
            "summer": (date(y, 5, 1), date(y, 8, 15)),
            "winter": (date(y - 1, 12, 15), date(y, 1, 31)),
        }[term.season]

    def ensure_term(self, term: TermInfo, catalog_year_id: int) -> int:
        lo, hi = self.default_term_dates(term)
        row = self.conn.execute(
            """INSERT INTO term (institution_id, catalog_year_id, code, name, season, year, dates)
               VALUES (%s, %s, %s, %s, %s, %s, daterange(%s, %s, '[]'))
               ON CONFLICT (institution_id, code) DO UPDATE SET name = EXCLUDED.name
               RETURNING term_id""",
            (self.institution_id, catalog_year_id, term.code, term.name, term.season, term.year, lo, hi),
        ).fetchone()
        return row[0]

    def update_term_dates(self, term_id: int, lo: date, hi: date) -> None:
        self.conn.execute("UPDATE term SET dates = daterange(%s, %s, '[]') WHERE term_id = %s", (lo, hi, term_id))

    def ensure_term_part(self, term_id: int, code: str, span: Optional[Tuple[date, date]]) -> int:
        key = (term_id, code)
        if key in self._term_parts and span is None:
            return self._term_parts[key]
        if span is None:
            span_row = self.conn.execute("SELECT lower(dates), upper(dates) - 1 FROM term WHERE term_id = %s", (term_id,)).fetchone()
            span = (span_row[0], span_row[1])
        row = self.conn.execute(
            """INSERT INTO term_part (term_id, code, name, dates)
               VALUES (%s, %s, %s, daterange(%s, %s, '[]'))
               ON CONFLICT (term_id, code) DO UPDATE
                 SET dates = daterange(least(lower(term_part.dates), lower(EXCLUDED.dates)),
                                       greatest(upper(term_part.dates), upper(EXCLUDED.dates)), '[)')
               RETURNING term_part_id""",
            (term_id, code, f"Part of term {code}", span[0], span[1]),
        ).fetchone()
        self._term_parts[key] = row[0]
        return row[0]

    # -- catalog ----------------------------------------------------------
    def ensure_course(self, catalog_year_id: int, rec: SectionRecord) -> int:
        key = (catalog_year_id, rec.subject_code, rec.course_number)
        if key in self._courses:
            return self._courses[key]
        row = self.conn.execute(
            """SELECT course_id FROM course_catalog_entry
               WHERE catalog_year_id = %s AND subject_code = %s AND course_number = %s""",
            key,
        ).fetchone()
        if row is None:
            # Same code in another catalog year => same course by default (renumbering is curated by hand).
            prior = self.conn.execute(
                """SELECT e.course_id FROM course_catalog_entry e
                   JOIN course c USING (course_id)
                   WHERE c.institution_id = %s AND e.subject_code = %s AND e.course_number = %s
                   ORDER BY e.catalog_year_id DESC LIMIT 1""",
                (self.institution_id, rec.subject_code, rec.course_number),
            ).fetchone()
            if prior:
                course_id = prior[0]
            else:
                course_id = self.conn.execute(
                    "INSERT INTO course (institution_id, notes) VALUES (%s, %s) RETURNING course_id",
                    (self.institution_id, f"created from section feed ({self.config.adapter})"),
                ).fetchone()[0]
            cmin = _dec(rec.credits_min) if rec.credits_min is not None else Decimal(0)
            cmax = _dec(rec.credits_max) if rec.credits_max is not None else cmin
            self.conn.execute(
                """INSERT INTO course_catalog_entry
                     (course_id, catalog_year_id, subject_code, course_number, level, title, credits_min, credits_max, is_primary)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                   ON CONFLICT (catalog_year_id, subject_code, course_number) DO NOTHING""",
                (course_id, catalog_year_id, rec.subject_code, rec.course_number,
                 course_level(rec.course_number, self.config.level_style), rec.title, cmin, cmax),
            )
            row = (course_id,)
        self._courses[key] = row[0]
        return row[0]

    # -- people / places --------------------------------------------------
    def ensure_instructor(self, rec: InstructorRecord) -> int:
        norm = normalize_name(rec.family_name, rec.given_name, rec.display_name)
        cache_key = f"id:{rec.external_id}" if rec.external_id else f"name:{norm}"
        if cache_key in self._instructors:
            return self._instructors[cache_key]

        row = None
        if rec.external_id:
            row = self.conn.execute(
                """INSERT INTO instructor (institution_id, external_id, display_name, family_name, given_name, email, name_normalized)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (institution_id, external_id) DO UPDATE
                     SET display_name = EXCLUDED.display_name,
                         family_name = COALESCE(EXCLUDED.family_name, instructor.family_name),
                         given_name = COALESCE(EXCLUDED.given_name, instructor.given_name),
                         email = COALESCE(EXCLUDED.email, instructor.email),
                         name_normalized = EXCLUDED.name_normalized
                   RETURNING instructor_id""",
                (self.institution_id, rec.external_id, rec.display_name, rec.family_name, rec.given_name, rec.email, norm),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT i.instructor_id FROM instructor i
                   LEFT JOIN instructor_name_alias a USING (instructor_id)
                   WHERE i.institution_id = %s AND (i.name_normalized = %s OR a.name_normalized = %s)
                   LIMIT 1""",
                (self.institution_id, norm, norm),
            ).fetchone()
            if row is None:
                row = self.conn.execute(
                    """INSERT INTO instructor (institution_id, external_id, display_name, family_name, given_name, email, name_normalized)
                       VALUES (%s, NULL, %s, %s, %s, %s, %s) RETURNING instructor_id""",
                    (self.institution_id, rec.display_name, rec.family_name, rec.given_name, rec.email, norm),
                ).fetchone()
        instructor_id = row[0]
        self.conn.execute(
            """INSERT INTO instructor_name_alias (instructor_id, name_normalized, source, confidence)
               VALUES (%s, %s, 'registrar', 1.0) ON CONFLICT (instructor_id, name_normalized) DO NOTHING""",
            (instructor_id, norm),
        )
        self._instructors[cache_key] = instructor_id
        return instructor_id

    def ensure_location(self, m: MeetingRecord) -> Optional[int]:
        if not m.has_location:
            return None
        key = (m.campus, m.building_code, m.room)
        if key in self._locations:
            return self._locations[key]
        row = self.conn.execute(
            """INSERT INTO location (institution_id, campus, building_code, building_name, room)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (institution_id, campus, building_code, room) DO UPDATE
                 SET building_name = COALESCE(EXCLUDED.building_name, location.building_name)
               RETURNING location_id""",
            (self.institution_id, m.campus, m.building_code, m.building_name, m.room),
        ).fetchone()
        self._locations[key] = row[0]
        return row[0]

    def ensure_crosslist_group(self, term_id: int, rec: SectionRecord) -> Optional[int]:
        if not rec.crosslist_code:
            return None
        key = (term_id, rec.crosslist_code)
        row = self.conn.execute(
            """INSERT INTO crosslist_group (term_id, registrar_code, combined_capacity, combined_enrolled)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (term_id, registrar_code) DO UPDATE
                 SET combined_capacity = EXCLUDED.combined_capacity, combined_enrolled = EXCLUDED.combined_enrolled
               RETURNING crosslist_group_id""",
            (term_id, rec.crosslist_code, rec.crosslist_capacity, rec.crosslist_enrolled),
        ).fetchone()
        self._crosslists[key] = row[0]
        return row[0]

    # -- sections ---------------------------------------------------------
    SECTION_COLS = ("term_part_id", "course_id", "section_code", "offered_subject_code", "offered_course_number",
                    "component", "modality", "credits_min", "credits_max", "capacity", "enrolled",
                    "waitlist_capacity", "waitlist_count", "status", "crosslist_group_id", "title")
    COUNTER_COLS = ("capacity", "enrolled", "waitlist_capacity", "waitlist_count", "status")

    def upsert_section(self, term_id: int, term_part_id: Optional[int], course_id: int,
                       crosslist_group_id: Optional[int], rec: SectionRecord, observed_at: datetime) -> SectionOutcome:
        new = {
            "term_part_id": term_part_id, "course_id": course_id, "section_code": rec.section_code,
            "offered_subject_code": rec.subject_code, "offered_course_number": rec.course_number,
            "component": rec.component, "modality": rec.modality,
            "credits_min": _dec(rec.credits_min), "credits_max": _dec(rec.credits_max),
            "capacity": rec.capacity, "enrolled": rec.enrolled,
            "waitlist_capacity": rec.waitlist_capacity, "waitlist_count": rec.waitlist_count,
            "status": rec.status, "crosslist_group_id": crosslist_group_id, "title": rec.title,
        }
        cols = ", ".join(self.SECTION_COLS)
        existing = self.conn.execute(
            f"SELECT section_id, {cols} FROM section WHERE term_id = %s AND crn = %s", (term_id, rec.crn)
        ).fetchone()

        if existing is None:
            placeholders = ", ".join(["%s"] * len(self.SECTION_COLS))
            section_id = self.conn.execute(
                f"""INSERT INTO section (term_id, crn, {cols}, first_seen_at, last_seen_at)
                    VALUES (%s, %s, {placeholders}, %s, %s) RETURNING section_id""",
                (term_id, rec.crn, *[new[c] for c in self.SECTION_COLS], observed_at, observed_at),
            ).fetchone()[0]
            return SectionOutcome(section_id, inserted=True, changed=True, counters_changed=True)

        section_id = existing[0]
        old = dict(zip(self.SECTION_COLS, existing[1:]))
        old["credits_min"], old["credits_max"] = _dec(old["credits_min"]), _dec(old["credits_max"])
        diff = [c for c in self.SECTION_COLS if old[c] != new[c]]
        if diff:
            sets = ", ".join(f"{c} = %s" for c in diff)
            self.conn.execute(
                f"UPDATE section SET {sets}, last_seen_at = %s WHERE section_id = %s",
                (*[new[c] for c in diff], observed_at, section_id),
            )
        else:
            self.conn.execute("UPDATE section SET last_seen_at = %s WHERE section_id = %s", (observed_at, section_id))
        return SectionOutcome(section_id, inserted=False, changed=bool(diff),
                              counters_changed=any(c in self.COUNTER_COLS for c in diff))

    def snapshot(self, section_id: int, rec: SectionRecord, observed_at: datetime) -> None:
        self.conn.execute(
            """INSERT INTO section_enrollment_snapshot (section_id, observed_at, capacity, enrolled, waitlist_count, status)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (section_id, observed_at, rec.capacity, rec.enrolled, rec.waitlist_count, rec.status),
        )

    def sync_instructors(self, section_id: int, desired: Sequence[Tuple[int, str]], observed_at: datetime) -> bool:
        """Close assignments that disappeared, open new ones. Returns True if anything changed."""
        current = {
            (r[0], r[1]): r[2] for r in self.conn.execute(
                "SELECT instructor_id, role, section_instructor_id FROM section_instructor WHERE section_id = %s AND upper_inf(effective)",
                (section_id,),
            )
        }
        want = set(desired)
        gone = [sid for key, sid in current.items() if key not in want]
        new = [key for key in want if key not in current]
        if gone:
            self.conn.execute(
                """UPDATE section_instructor SET effective = tstzrange(lower(effective), %s, '[)')
                   WHERE section_instructor_id = ANY(%s)""",
                (observed_at, gone),
            )
        for instructor_id, role in new:
            self.conn.execute(
                """INSERT INTO section_instructor (section_id, instructor_id, role, effective, source)
                   VALUES (%s, %s, %s, tstzrange(%s, NULL, '[)'), %s)""",
                (section_id, instructor_id, role, observed_at, self.config.adapter),
            )
        return bool(gone or new)

    def sync_meetings(self, section_id: int, meetings: Sequence[MeetingRecord]) -> bool:
        """Replace the section's meeting blocks if the set differs. Slots rebuild via trigger."""
        desired = []
        for m in meetings:
            loc = self.ensure_location(m)
            desired.append((m.kind, m.modality, list(m.days) if m.days else None, m.start_time, m.end_time,
                            m.start_date, m.end_date + timedelta(days=1), m.time_tba, loc, m.location_tba, m.location_text))
        existing = [
            tuple(r) for r in self.conn.execute(
                """SELECT kind, modality, days, start_time, end_time, lower(dates), upper(dates),
                          time_tba, location_id, location_tba, location_text
                   FROM meeting_block WHERE section_id = %s""",
                (section_id,),
            )
        ]
        norm = lambda rows: sorted(repr(r) for r in rows)  # noqa: E731
        if norm(existing) == norm(desired):
            return False
        self.conn.execute("DELETE FROM meeting_block WHERE section_id = %s", (section_id,))
        for d in desired:
            kind, modality, days, st, et, lo, hi, tba, loc, loc_tba, loc_text = d
            self.conn.execute(
                """INSERT INTO meeting_block
                     (section_id, kind, modality, days, start_time, end_time, dates, time_tba, location_id, location_tba, location_text)
                   VALUES (%s, %s, %s, %s, %s, %s, daterange(%s, %s, '[)'), %s, %s, %s, %s)""",
                (section_id, kind, modality, days, st, et, lo, hi, tba, loc, loc_tba, loc_text),
            )
        return True

    # -- end of run -------------------------------------------------------
    def unseen_sections(self, term_id: int, run_started_at: datetime) -> List[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT crn FROM section WHERE term_id = %s AND last_seen_at < %s AND status <> 'cancelled' ORDER BY crn",
            (term_id, run_started_at),
        )]

    def mark_cancelled(self, term_id: int, crns: Iterable[str], observed_at: datetime) -> int:
        crns = list(crns)
        if not crns:
            return 0
        cur = self.conn.execute(
            "UPDATE section SET status = 'cancelled' WHERE term_id = %s AND crn = ANY(%s)", (term_id, crns)
        )
        for crn in crns:
            self.conn.execute(
                """INSERT INTO section_enrollment_snapshot (section_id, observed_at, capacity, enrolled, waitlist_count, status)
                   SELECT section_id, %s, capacity, enrolled, waitlist_count, 'cancelled' FROM section WHERE term_id = %s AND crn = %s
                   ON CONFLICT DO NOTHING""",
                (observed_at, term_id, crn),
            )
        return cur.rowcount
