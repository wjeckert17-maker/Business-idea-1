"""Normalized records every adapter produces. The loader knows only these."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Optional

MODALITIES = ("in_person", "online_sync", "online_async", "hybrid")
COMPONENTS = ("lecture", "lab", "discussion", "seminar", "studio", "independent", "other")
MEETING_KINDS = ("class", "lab", "discussion", "exam", "other")


class ParseError(Exception):
    """The adapter could not turn a raw payload into a SectionRecord."""


@dataclass(frozen=True)
class TermInfo:
    code: str          # registrar code, e.g. '202608'
    name: str          # 'Fall 2026'
    season: str        # fall | winter | spring | summer
    year: int


@dataclass(frozen=True)
class InstructorRecord:
    external_id: Optional[str]
    display_name: str
    email: Optional[str] = None
    family_name: Optional[str] = None
    given_name: Optional[str] = None
    role: str = "primary"      # primary | secondary | ta


@dataclass(frozen=True)
class MeetingRecord:
    kind: str                          # class | lab | discussion | exam | other
    modality: str                      # in_person | online_sync | online_async
    days: Optional[tuple]              # ISO weekdays 1..7, None when async/TBA/single-date
    start_time: Optional[time]
    end_time: Optional[time]
    start_date: date
    end_date: date                     # inclusive
    time_tba: bool = False
    campus: Optional[str] = None
    building_code: Optional[str] = None
    building_name: Optional[str] = None
    room: Optional[str] = None
    location_tba: bool = False
    location_text: Optional[str] = None

    @property
    def has_location(self) -> bool:
        return bool(self.building_code or self.room)


@dataclass(frozen=True)
class UnrecognizedPattern:
    crn: str
    reason: str
    raw: Any


@dataclass
class SectionRecord:
    crn: str
    subject_code: str
    course_number: str
    section_code: str
    title: str
    component: str
    modality: str
    credits_min: Optional[float]
    credits_max: Optional[float]
    capacity: Optional[int]
    enrolled: Optional[int]
    waitlist_capacity: Optional[int]
    waitlist_count: Optional[int]
    status: str                         # open | closed | waitlist | cancelled
    part_of_term_code: Optional[str] = None
    crosslist_code: Optional[str] = None
    crosslist_capacity: Optional[int] = None
    crosslist_enrolled: Optional[int] = None
    instructors: list = field(default_factory=list)      # list[InstructorRecord]
    meetings: list = field(default_factory=list)         # list[MeetingRecord]
    unrecognized: list = field(default_factory=list)     # list[UnrecognizedPattern]
    unmapped_codes: list = field(default_factory=list)   # list[tuple[str, str]] (field, code)
