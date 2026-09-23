"""Ellucian Banner 9 Student Registration Self-Service ("StudentRegistrationSsb").

Endpoints (all JSON, all behind the public class-search UI, no login):
  GET  classSearch/getTerms?searchTerm=&offset=1&max=N     -> [{code, description}]
  POST term/search?mode=search  (form: term=CODE)          -> binds the term to the session cookie
  GET  searchResults/searchResults?txt_term=CODE&pageOffset=K&pageMaxSize=500&...
                                                           -> {totalCount, data:[section...]}
  POST classSearch/resetDataForm                           -> clears server-side search state

Verified against registration.banner.gatech.edu on 2026-09-09: pageMaxSize=500 honored,
times are 'HHMM', dates 'MM/DD/YYYY', weekdays are seven booleans, faculty carry bannerId.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..meetings import UnrecognizedMeeting, normalize_meeting
from ..models import InstructorRecord, ParseError, SectionRecord, TermInfo, UnrecognizedPattern
from .base import RawSection, SourceAdapter, register

log = logging.getLogger(__name__)

WEEKDAY_FIELDS = (("monday", 1), ("tuesday", 2), ("wednesday", 3), ("thursday", 4),
                  ("friday", 5), ("saturday", 6), ("sunday", 7))

COMPONENT_KEYWORDS = (
    ("lecture", "lecture"), ("lab", "lab"), ("recitation", "discussion"),
    ("discussion", "discussion"), ("breakout", "discussion"), ("seminar", "seminar"),
    ("studio", "studio"), ("directed study", "independent"), ("independent", "independent"),
    ("thesis", "independent"), ("research", "independent"), ("dissertation", "independent"),
    ("internship", "independent"), ("practicum", "independent"),
)

SEASON_WORDS = {"fall": "fall", "autumn": "fall", "spring": "spring", "summer": "summer", "winter": "winter"}


@register
class Banner9Adapter(SourceAdapter):
    name = "banner9"

    # -- terms ------------------------------------------------------------
    def list_terms(self) -> List[TermInfo]:
        resp = self.http.get("classSearch/getTerms", params={"searchTerm": "", "offset": 1, "max": 100})
        terms = []
        for row in resp.json():
            info = self.term_from_code(row["code"], row["description"])
            if info is not None:
                terms.append(info)
        return terms

    @staticmethod
    def term_from_code(code: str, description: str) -> Optional[TermInfo]:
        """'Fall 2026' / 'Spring 2027 (View Only)' -> TermInfo. Non-standard terms return None."""
        desc = description.lower()
        year = re.search(r"(20\d\d)", desc)
        season = next((s for w, s in SEASON_WORDS.items() if w in desc), None)
        if not year or not season:
            return None
        name = re.sub(r"\s*\(view only\)\s*", "", description, flags=re.I).strip()
        return TermInfo(code=code, name=name, season=season, year=int(year.group(1)))

    # -- sections ---------------------------------------------------------
    def fetch_sections(self, term: TermInfo) -> Iterator[RawSection]:
        self.http.post("classSearch/resetDataForm")
        self.http.post("term/search", params={"mode": "search"},
                       data={"term": term.code, "studyPath": "", "studyPathText": "",
                             "startDatepicker": "", "endDatepicker": ""})
        page_size = self.config.page_size
        offset, total = 0, None
        seen_crns = set()
        while total is None or offset < total:
            resp = self.http.get("searchResults/searchResults", params={
                "txt_term": term.code, "startDatepicker": "", "endDatepicker": "",
                "pageOffset": offset, "pageMaxSize": page_size,
                "sortColumn": "subjectDescription", "sortDirection": "asc",
            })
            body = resp.json()
            if not body.get("success", True):
                raise RuntimeError(f"search returned success=false at offset {offset}: {json.dumps(body)[:500]}")
            data = body.get("data") or []
            total = int(body.get("totalCount") or 0)
            log.info("term %s: page offset=%d got %d rows (total %d)", term.code, offset, len(data), total)
            if not data:
                break
            for row in data:
                crn = str(row.get("courseReferenceNumber", "?"))
                if crn in seen_crns:      # Banner paging is stable per session, but be safe
                    continue
                seen_crns.add(crn)
                yield RawSection(source_key=crn, payload=row)
            offset += len(data)

    def finish(self, term: TermInfo) -> None:
        try:
            self.http.post("classSearch/resetDataForm")
        except Exception as exc:  # cleanup only
            log.debug("resetDataForm failed: %s", exc)

    # -- parsing ----------------------------------------------------------
    def parse_section(self, term: TermInfo, raw: RawSection) -> SectionRecord:
        r = raw.payload
        try:
            return self._parse(term, r)
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"{type(exc).__name__}: {exc}") from exc

    def _parse(self, term: TermInfo, r: Dict[str, Any]) -> SectionRecord:
        crn = _req(r, "courseReferenceNumber")
        subject = _req(r, "subject")
        number = _req(r, "courseNumber")
        unmapped: List[Tuple[str, str]] = []

        component = self._component(r.get("scheduleTypeDescription"), unmapped)
        section_modality = self._section_modality(r, unmapped)

        credits_min, credits_max = self._credits(r)
        capacity = _int(r.get("maximumEnrollment"))
        enrolled = _int(r.get("enrollment"))
        wait_cap = _int(r.get("waitCapacity"))
        wait_count = _int(r.get("waitCount"))
        status = self._status(r, capacity, enrolled, wait_count)

        instructors = []
        for f in r.get("faculty") or []:
            name = (f.get("displayName") or "").strip()
            if not name:
                continue
            family, given = _split_name(name)
            instructors.append(InstructorRecord(
                external_id=_str(f.get("bannerId")), display_name=name,
                email=_str(f.get("emailAddress")), family_name=family, given_name=given,
                role="primary" if f.get("primaryIndicator") else "secondary",
            ))

        meetings, unrecognized = [], []
        for mf in r.get("meetingsFaculty") or []:
            mt = mf.get("meetingTime") or {}
            try:
                meetings.append(self._meeting(mt, component, section_modality, unmapped))
            except UnrecognizedMeeting as exc:
                unrecognized.append(UnrecognizedPattern(crn=crn, reason=exc.reason, raw=mt))

        # Refine section modality from the evidence when the feed gave us no code.
        if section_modality is None:
            timed = [m for m in meetings if m.start_time is not None]
            if not meetings:
                section_modality = "in_person"
            elif all(m.modality == "online_async" for m in meetings):
                section_modality = "online_async"
            elif timed and any(m.modality == "online_sync" for m in timed) and all(m.modality != "in_person" for m in timed):
                section_modality = "online_sync"
            elif timed and any(m.modality == "online_sync" for m in timed):
                section_modality = "hybrid"
            else:
                section_modality = "in_person"

        return SectionRecord(
            crn=crn, subject_code=subject, course_number=number,
            section_code=_str(r.get("sequenceNumber")) or "0",
            title=(r.get("courseTitle") or "").strip() or f"{subject} {number}",
            component=component, modality=section_modality,
            credits_min=credits_min, credits_max=credits_max,
            capacity=capacity, enrolled=enrolled,
            waitlist_capacity=wait_cap, waitlist_count=wait_count, status=status,
            part_of_term_code=_str(r.get("partOfTerm")),
            crosslist_code=_str(r.get("crossList")),
            crosslist_capacity=_int(r.get("crossListCapacity")),
            crosslist_enrolled=_int(r.get("crossListCount")),
            instructors=instructors, meetings=meetings,
            unrecognized=unrecognized, unmapped_codes=unmapped,
        )

    # -- field helpers ----------------------------------------------------
    def _component(self, sched: Optional[str], unmapped: list) -> str:
        s = (sched or "").rstrip("*").strip().lower()
        for kw, comp in COMPONENT_KEYWORDS:
            if kw in s:
                return comp
        if s:
            unmapped.append(("scheduleTypeDescription", sched))
        return "other"

    def _section_modality(self, r: Dict[str, Any], unmapped: list) -> Optional[str]:
        code = _str(r.get("instructionalMethod"))
        if code is None:
            return None
        mapping = self.config.modality_maps.get("instructional_method", {})
        if code in mapping:
            return mapping[code]
        desc = (r.get("instructionalMethodDescription") or "").lower()
        if "async" in desc:
            return "online_async"
        if "hybrid" in desc or "blend" in desc:
            return "hybrid"
        if any(w in desc for w in ("online", "distance", "remote", "virtual")):
            return "online_sync"
        unmapped.append(("instructionalMethod", f"{code}|{r.get('instructionalMethodDescription')}"))
        return None

    def _block_modality(self, mt: Dict[str, Any], unmapped: list) -> Optional[str]:
        code = _str(mt.get("meetingScheduleType"))
        if code is None:
            return None
        mapping = self.config.modality_maps.get("meeting_schedule_type", {})
        if code in mapping:
            return mapping[code]
        unmapped.append(("meetingScheduleType", code))
        return None

    def _meeting(self, mt: Dict[str, Any], component: str, section_modality: Optional[str], unmapped: list):
        mtype = (mt.get("meetingType") or "").upper()
        mdesc = (mt.get("meetingTypeDescription") or "").lower()
        if "exam" in mdesc or mtype == "EXAM":
            kind = "exam"
        elif component in ("lab", "discussion"):
            kind = component
        elif mtype == "CLAS" or "class" in mdesc:
            kind = "class"
        else:
            kind = "other"
        building = _str(mt.get("building"))
        room = _str(mt.get("room"))
        if building and building.upper() == "TBA":
            building = None
        return normalize_meeting(
            kind=kind,
            section_modality=section_modality or "in_person",
            block_modality=self._block_modality(mt, unmapped),
            days=[n for f, n in WEEKDAY_FIELDS if mt.get(f)],
            start_time=_hhmm(mt.get("beginTime")),
            end_time=_hhmm(mt.get("endTime")),
            start_date=_mdy(mt.get("startDate")),
            end_date=_mdy(mt.get("endDate")),
            campus=_str(mt.get("campusDescription")) or _str(mt.get("campus")),
            building_code=building, building_name=_str(mt.get("buildingDescription")), room=room,
            location_text=None,
        )

    @staticmethod
    def _credits(r: Dict[str, Any]):
        lo = _num(r.get("creditHourLow"))
        hi = _num(r.get("creditHourHigh"))
        fixed = _num(r.get("creditHours"))
        if fixed is not None and lo is None and hi is None:
            return fixed, fixed
        if lo is None and hi is None:
            return None, None
        lo = lo if lo is not None else hi
        hi = hi if hi is not None else lo
        return min(lo, hi), max(lo, hi)

    @staticmethod
    def _status(r, capacity, enrolled, wait_count) -> str:
        if r.get("openSection") is True:
            return "open"
        if wait_count and capacity is not None and enrolled is not None and enrolled >= capacity:
            return "waitlist"
        return "closed"


# -- tiny coercions ---------------------------------------------------------
def _req(r: Dict[str, Any], key: str) -> str:
    v = r.get(key)
    if v is None or str(v).strip() == "":
        raise ParseError(f"missing required field {key!r}")
    return str(v).strip()


def _str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    return int(float(v))


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    return float(v)


def _hhmm(v) -> Optional[time]:
    s = _str(v)
    if s is None:
        return None
    if not re.fullmatch(r"\d{4}", s):
        raise UnrecognizedMeeting(f"unparsable time {s!r}")
    h, m = int(s[:2]), int(s[2:])
    if h > 23 or m > 59:
        raise UnrecognizedMeeting(f"time out of range {s!r}")
    return time(h, m)


def _mdy(v) -> Optional[date]:
    s = _str(v)
    if s is None:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        raise UnrecognizedMeeting(f"unparsable date {s!r}")


def _split_name(display: str):
    if "," in display:
        family, given = display.split(",", 1)
        return family.strip() or None, given.strip() or None
    parts = display.split()
    if len(parts) >= 2:
        return parts[-1], " ".join(parts[:-1])
    return display, None
