"""Runner behaviour with a fake adapter and a fake loader (no network, no database)."""
import io
import json
from contextlib import contextmanager
from datetime import date

from course_ingest.adapters.base import RawSection, SourceAdapter
from course_ingest.config import SchoolConfig
from course_ingest.loader import SectionOutcome
from course_ingest.models import MeetingRecord, ParseError, SectionRecord, TermInfo, UnrecognizedPattern
from course_ingest.runner import TermRun

TERM = TermInfo("202608", "Fall 2026", "fall", 2026)
CFG = SchoolConfig(slug="test", name="Test U", timezone="UTC", adapter="fake", base_url="https://x/", contact="t@x")


class FakeHttp:
    requests_made = 4
    retries = 1


def section(crn, **kw):
    base = dict(crn=crn, subject_code="CS", course_number="101", section_code="A", title="Intro",
                component="lecture", modality="in_person", credits_min=3, credits_max=3, capacity=10,
                enrolled=1, waitlist_capacity=0, waitlist_count=0, status="open", part_of_term_code="1")
    base.update(kw)
    return SectionRecord(**base)


class FakeAdapter(SourceAdapter):
    name = "fake"

    def __init__(self, records):
        super().__init__(FakeHttp(), CFG)
        self.records = records
        self.finished = False

    def list_terms(self):
        return [TERM]

    def fetch_sections(self, term):
        for crn, _ in self.records:
            yield RawSection(crn, {"crn": crn, "junk": True})

    def parse_section(self, term, raw):
        rec = dict(self.records)[raw.source_key]
        if isinstance(rec, Exception):
            raise rec
        return rec

    def finish(self, term):
        self.finished = True


class FakeConn:
    def __init__(self):
        self.commits = 0

    @contextmanager
    def transaction(self):
        yield

    def commit(self):
        self.commits += 1


class FakeLoader:
    """Pretends CRN '2' already exists unchanged and '3' exists with a changed instructor."""

    def __init__(self):
        self.conn = FakeConn()
        self.cancelled = []
        self.snapshots = []
        self.resets = 0

    def reset_caches(self): self.resets += 1
    def ensure_institution(self): return 1
    def start_batch(self, source, at): return 7
    def finish_batch(self, bid, notes): self.batch_notes = json.loads(notes)
    def ensure_catalog_year(self, term): return 1
    def ensure_term(self, term, cy): return 11
    def ensure_term_part(self, term_id, code, span): return 5
    def ensure_course(self, cy, rec):
        if rec.crn == "boom":
            raise RuntimeError("db exploded")
        return 3
    def ensure_crosslist_group(self, term_id, rec): return None
    def ensure_instructor(self, rec): return 9

    def upsert_section(self, term_id, part, course, xl, rec, now):
        if rec.crn == "1":
            return SectionOutcome(101, inserted=True, changed=True, counters_changed=True)
        return SectionOutcome(int(rec.crn), inserted=False, changed=False, counters_changed=False)

    def sync_instructors(self, sid, desired, now): return sid == 3
    def sync_meetings(self, sid, meetings): return False
    def snapshot(self, sid, rec, now): self.snapshots.append(sid)
    def unseen_sections(self, term_id, started): return ["999"]
    def mark_cancelled(self, term_id, crns, now): self.cancelled = list(crns); return len(self.cancelled)
    def update_term_dates(self, term_id, lo, hi): self.term_dates = (lo, hi)


def test_counts_failures_and_summary():
    m = MeetingRecord(kind="class", modality="in_person", days=(1, 3), start_time=None, end_time=None,
                      start_date=date(2026, 8, 24), end_date=date(2026, 12, 17))
    records = [
        ("1", section("1", meetings=[m])),
        ("2", section("2", unmapped_codes=[("scheduleTypeDescription", "Colloquium*")])),
        ("3", section("3", unrecognized=[UnrecognizedPattern("3", "weekdays given but no times", {"x": 1})])),
        ("bad", ParseError("missing required field 'subject'")),
        ("boom", section("boom")),
    ]
    adapter = FakeAdapter(records)
    loader = FakeLoader()
    ff = io.StringIO()
    s = TermRun(adapter, loader, TERM, failures_file=ff, mark_missing_cancelled=True).run()

    assert (s.seen, s.inserted, s.updated, s.unchanged, s.failed) == (5, 1, 1, 1, 2)
    assert s.complete and s.error is None and adapter.finished
    assert s.missing == 1 and s.cancelled_marked == 1 and loader.cancelled == ["999"]
    assert s.unrecognized_meeting_patterns == [{"crn": "3", "reason": "weekdays given but no times", "raw": {"x": 1}}]
    assert s.unmapped_codes == {"scheduleTypeDescription": {"Colloquium*": 1}}
    assert s.requests_made == 4 and s.retries == 1
    assert loader.snapshots == [101] and loader.term_dates == (date(2026, 8, 24), date(2026, 12, 17))
    assert loader.resets == 1  # caches dropped after the failed transaction

    lines = [json.loads(l) for l in ff.getvalue().splitlines()]
    assert [l["crn"] for l in lines] == ["bad", "boom"]
    assert lines[0]["raw"] == {"crn": "bad", "junk": True} and "missing required field" in lines[0]["error"]
    assert "db exploded" in lines[1]["error"]
    assert loader.batch_notes["failed"] == 2 and "failures" not in loader.batch_notes


def test_dry_run_and_limit():
    adapter = FakeAdapter([("1", section("1")), ("2", section("2")), ("bad", ParseError("x"))])
    s = TermRun(adapter, None, TERM, limit=2).run()
    assert s.dry_run and s.seen == 2 and not s.complete and s.failed == 0


def test_fetch_error_marks_run_incomplete():
    class Broken(FakeAdapter):
        def fetch_sections(self, term):
            yield RawSection("1", {})
            raise ConnectionError("site went away")
    s = TermRun(Broken([("1", section("1"))]), None, TERM).run()
    assert s.seen == 1 and not s.complete and "site went away" in s.error
