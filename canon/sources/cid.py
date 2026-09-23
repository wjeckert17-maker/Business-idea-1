"""C-ID (Course Identification Numbering System, California): CCC courses approved against a C-ID
descriptor. Raw cache: data/cid/page_NNN.json from /api/v1/course-list.

Each approved (course, descriptor) -> member_of hub ('cid', descriptor). Effective from the approval
(cor_effective_date). NOT exhaustive: a college may simply not have submitted a course, so no coverage
records are emitted and absence is never a negative.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Iterator

from ..models import AssertionRecord, CourseRef, InstitutionRef, MEMBER_OF
from ..text import parse_units
from .base import GroundTruthAdapter, register


@register
class CidAdapter(GroundTruthAdapter):
    code = "cid"
    name = "C-ID approved courses (California)"
    kind = "common_numbering"
    base_trust = 0.95
    notes = "Faculty-reviewed descriptor approval; CSU accepts approved courses as the descriptor."

    def records(self) -> Iterator:
        for fn in sorted(glob.glob(os.path.join(self.path, "page_*.json"))):
            body = json.load(open(fn))
            for row in body.get("data") or []:
                d = row.get("descriptor_cache") or {}
                cid = (row.get("cid_number") or d.get("cid_name") or "").strip()
                if " " not in cid:
                    continue
                prefix, num = cid.split(" ", 1)
                hub = CourseRef(subject=prefix, number=num, system_code="cid", title=d.get("name"),
                                attrs={"discipline": d.get("discipline_name"), "descriptor_id": d.get("id")})
                yield hub
                schools = {s["id"]: s for s in row.get("schools_cache") or []}
                for c in row.get("courses_cache") or []:
                    sname = c.get("school_name") or ""
                    sid = next((s["id"] for s in schools.values() if s["name"] == sname), None)
                    inst = InstitutionRef("cid", str(sid or sname), sname, state="CA", kind="community_college")
                    lo, hi = parse_units(c.get("course_units"))
                    course = CourseRef(subject=c.get("course_department") or "", number=str(c.get("course_number") or ""),
                                       institution=inst, title=c.get("course_name"), units_min=lo, units_max=hi,
                                       effective_from=c.get("cor_effective_date"), status="active" if c.get("is_active") else "inactive",
                                       attrs={"cid_course_id": c.get("id"), "honors": c.get("is_honor_course")})
                    if not course.subject or not course.number:
                        continue
                    yield course
                    yield AssertionRecord(course, hub, MEMBER_OF, 1.0, f"cid:course:{c.get('id')}",
                                          c.get("cor_effective_date"), None,
                                          evidence={"approved": c.get("cor_approval_date"), "active": c.get("is_active")})
