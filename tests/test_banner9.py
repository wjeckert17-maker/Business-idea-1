"""Parser tests on payloads captured from registration.banner.gatech.edu (2026-09-09)."""
import copy
from datetime import date, time
from pathlib import Path

import pytest

from course_ingest.adapters.banner9 import Banner9Adapter
from course_ingest.adapters.base import RawSection
from course_ingest.config import SchoolConfig
from course_ingest.models import ParseError, TermInfo

CFG = SchoolConfig.load(Path(__file__).resolve().parents[1] / "schools" / "gatech.toml")
TERM = TermInfo("202608", "Fall 2026", "fall", 2026)

MEETING = {
    "beginTime": "1230", "endTime": "1345", "building": "172", "buildingDescription": "Scheller College of Business",
    "campus": "A", "campusDescription": "Georgia Tech-Atlanta *", "category": "01", "creditHourSession": 3,
    "startDate": "08/24/2026", "endDate": "12/17/2026", "hoursWeek": 2.5, "meetingScheduleType": "A",
    "meetingType": "CLAS", "meetingTypeDescription": "Class", "room": "103", "term": "202608",
    "monday": True, "tuesday": False, "wednesday": True, "thursday": False, "friday": False, "saturday": False, "sunday": False,
}
ACCT = {
    "courseReferenceNumber": "84113", "subject": "ACCT", "courseNumber": "2101", "sequenceNumber": "A",
    "courseTitle": "Accounting I", "scheduleTypeDescription": "Lecture*", "instructionalMethod": None,
    "instructionalMethodDescription": None, "creditHours": 3, "creditHourLow": 3, "creditHourHigh": None,
    "maximumEnrollment": 60, "enrollment": 58, "seatsAvailable": 2, "waitCapacity": 10, "waitCount": 0,
    "openSection": True, "partOfTerm": "1", "crossList": None, "crossListCapacity": None, "crossListCount": None,
    "faculty": [{"bannerId": "66871", "displayName": "Thayer, Jane", "emailAddress": "jane.thayer@scheller.gatech.edu",
                 "primaryIndicator": True}],
    "meetingsFaculty": [{"meetingTime": MEETING}],
}
ARRANGED = {
    "courseReferenceNumber": "84605", "subject": "AE", "courseNumber": "2010", "sequenceNumber": "R",
    "courseTitle": "Thermo & Fluids Fund", "scheduleTypeDescription": "Lecture*", "instructionalMethod": None,
    "creditHours": None, "creditHourLow": 4, "creditHourHigh": None, "maximumEnrollment": 5, "enrollment": 5,
    "waitCapacity": 0, "waitCount": 0, "openSection": False, "partOfTerm": "D",
    "faculty": [{"bannerId": "66868", "displayName": "Tsikata, Sedina", "emailAddress": None, "primaryIndicator": True}],
    "meetingsFaculty": [{"meetingTime": {**MEETING, "beginTime": None, "endTime": None, "building": None, "room": None,
                                         "monday": False, "wednesday": False, "campusDescription": None}}],
}


def adapter():
    return Banner9Adapter(http=None, config=CFG)


def parse(payload):
    return adapter().parse_section(TERM, RawSection(payload.get("courseReferenceNumber", "?"), payload))


def test_regular_lecture():
    rec = parse(ACCT)
    assert rec.crn == "84113" and rec.subject_code == "ACCT" and rec.course_number == "2101"
    assert rec.component == "lecture" and rec.modality == "in_person" and rec.status == "open"
    assert (rec.credits_min, rec.credits_max) == (3.0, 3.0)
    assert (rec.capacity, rec.enrolled, rec.waitlist_capacity, rec.waitlist_count) == (60, 58, 10, 0)
    assert len(rec.instructors) == 1
    ins = rec.instructors[0]
    assert (ins.external_id, ins.family_name, ins.given_name, ins.role) == ("66871", "Thayer", "Jane", "primary")
    assert len(rec.meetings) == 1
    m = rec.meetings[0]
    assert m.days == (1, 3) and m.start_time == time(12, 30) and m.end_time == time(13, 45)
    assert m.start_date == date(2026, 8, 24) and m.end_date == date(2026, 12, 17)
    assert m.kind == "class" and m.modality == "in_person" and not m.time_tba
    assert (m.building_code, m.room, m.building_name) == ("172", "103", "Scheller College of Business")
    assert not rec.unrecognized and not rec.unmapped_codes


def test_arranged_time_is_tba_not_a_failure():
    rec = parse(ARRANGED)
    assert rec.status == "closed" and (rec.credits_min, rec.credits_max) == (4.0, 4.0)
    m = rec.meetings[0]
    assert m.time_tba and m.days is None and m.start_time is None and m.location_tba
    assert rec.modality == "in_person"


def test_online_async_from_instructional_method():
    p = copy.deepcopy(ARRANGED)
    p["instructionalMethod"], p["instructionalMethodDescription"] = "F", "Fully at a Distance (BOR)"
    rec = parse(p)
    assert rec.modality == "online_async"
    assert rec.meetings[0].modality == "online_async" and not rec.meetings[0].time_tba


def test_single_date_meeting_has_no_days():
    p = copy.deepcopy(ACCT)
    mt = p["meetingsFaculty"][0]["meetingTime"]
    mt.update({"startDate": "10/03/2026", "endDate": "10/03/2026", "monday": False, "wednesday": False,
               "meetingType": "EXAM", "meetingTypeDescription": "Exam"})
    rec = parse(p)
    m = rec.meetings[0]
    assert m.days is None and m.start_date == m.end_date == date(2026, 10, 3) and m.kind == "exam"


def test_unrecognized_pattern_skips_block_keeps_section():
    p = copy.deepcopy(ACCT)
    p["meetingsFaculty"][0]["meetingTime"]["endTime"] = "1200"      # ends before it starts
    p["meetingsFaculty"].append({"meetingTime": {**MEETING, "monday": False, "wednesday": False}})  # times, no days
    rec = parse(p)
    assert rec.meetings == []
    assert [u.reason for u in rec.unrecognized] == ["end time not after start time",
                                                    "times given but no weekdays on a multi-day span"]
    assert rec.unrecognized[0].raw["endTime"] == "1200"


def test_unmapped_codes_are_reported():
    p = copy.deepcopy(ACCT)
    p["scheduleTypeDescription"] = "Colloquium*"
    p["meetingsFaculty"][0]["meetingTime"]["meetingScheduleType"] = "Z"
    rec = parse(p)
    assert rec.component == "other"
    assert ("scheduleTypeDescription", "Colloquium*") in rec.unmapped_codes
    assert ("meetingScheduleType", "Z") in rec.unmapped_codes


def test_missing_crn_is_parse_error():
    p = copy.deepcopy(ACCT)
    del p["courseReferenceNumber"]
    with pytest.raises(ParseError):
        parse(p)


def test_garbage_time_is_unrecognized_not_fatal():
    p = copy.deepcopy(ACCT)
    p["meetingsFaculty"][0]["meetingTime"]["beginTime"] = "12:30pm"
    rec = parse(p)
    assert rec.meetings == [] and "unparsable time" in rec.unrecognized[0].reason


@pytest.mark.parametrize("code,desc,expect", [
    ("202608", "Fall 2026", ("fall", 2026, "Fall 2026")),
    ("202702", "Spring 2027 (View Only)", ("spring", 2027, "Spring 2027")),
    ("202623", "Language Institute 2026 (View Only)", None),
])
def test_term_from_code(code, desc, expect):
    t = Banner9Adapter.term_from_code(code, desc)
    if expect is None:
        assert t is None
    else:
        assert (t.season, t.year, t.name) == expect


def test_arranged_on_named_days_is_time_tba_with_days():
    """Seen live at GT (crn 88789): MF, 12 hrs/week, no clock time."""
    p = copy.deepcopy(ACCT)
    p["meetingsFaculty"][0]["meetingTime"].update({"beginTime": None, "endTime": None, "friday": True})
    rec = parse(p)
    m = rec.meetings[0]
    assert m.time_tba and m.days == (1, 3, 5) and m.start_time is None and not rec.unrecognized
