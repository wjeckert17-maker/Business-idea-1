"""ASSIST (assist.org): California's verified CCC -> CSU/UC articulation agreements.

Raw cache layout (written by fetch, read here): data/assist/institutions.json, academic_years.json,
agreement_<yearId>_<sendingId>_to_<receivingId>.json (the AllDepartments report).

Semantics extracted
  * receiving course R, sendingArticulation.items = groups joined by courseGroupConjunctions;
    inside a group, courses joined by courseConjunction ('And' | 'Or').
      - group with one course C            -> C satisfies R               (relation satisfies, conf 1.0)
      - 'Or' group                         -> each member satisfies R
      - 'And' group with k>1 courses       -> each member satisfies_jointly R, sharing group_key
  * deniedCourses                          -> denied (explicit negative)
  * noArticulationReason with no items     -> not_articulated from a synthetic institution-level node? No:
    recorded as a coverage fact only; the receiving course simply has no positive edge from that college.
  * every fetched agreement                -> coverage(sending, receiving, academic year) — exhaustive.
  * Series type: receiving side is several courses; we emit satisfies_jointly toward each member.
Effective range: the agreement's academic year (Oct 1 -> Oct 1 per ASSIST's beginDate/endDate).
"""
from __future__ import annotations

import glob
import json
import os
import re
from typing import Dict, Iterator, List, Optional

from ..models import AssertionRecord, CourseRef, CoverageRecord, InstitutionRef, SATISFIES, SATISFIES_JOINTLY, DENIED
from .base import GroundTruthAdapter, register


def _inst_ref(inst: Dict) -> InstitutionRef:
    names = inst.get("names") or []
    name = names[-1]["name"] if names else inst.get("name") or f"ASSIST {inst['id']}"
    kind = "community_college" if inst.get("isCommunityCollege") else "university"
    return InstitutionRef("assist", str(inst["id"]), name, state="CA", kind=kind)


def _term_to_date(code: Optional[str]) -> Optional[str]:
    """'F2022' -> '2022-08-01', 'S2019' -> '2019-01-01', 'Su2021' -> '2021-06-01'."""
    if not code:
        return None
    m = re.match(r"^(F|S|Su|W)(\d{4})$", code)
    if not m:
        return None
    month = {"F": "08", "S": "01", "Su": "06", "W": "01"}[m.group(1)]
    return f"{m.group(2)}-{month}-01"


def _course_ref(c: Dict, inst: InstitutionRef) -> CourseRef:
    return CourseRef(
        subject=c.get("prefix") or "", number=str(c.get("courseNumber") or ""), institution=inst,
        title=(c.get("courseTitle") or "").strip() or None,
        units_min=c.get("minUnits"), units_max=c.get("maxUnits"),
        effective_from=_term_to_date(c.get("begin")), effective_to=_term_to_date(c.get("end")),
        attrs={"assist_course_id": c.get("courseIdentifierParentId"), "department": c.get("department")},
    )


@register
class AssistAdapter(GroundTruthAdapter):
    code = "assist"
    name = "ASSIST articulation agreements (California)"
    kind = "articulation"
    base_trust = 1.0
    notes = "Human-verified by articulation officers at the receiving institution."

    def records(self) -> Iterator:
        insts = {i["id"]: i for i in json.load(open(os.path.join(self.path, "institutions.json")))}
        years = {y["id"]: y for y in json.load(open(os.path.join(self.path, "academic_years.json")))}
        files = sorted(glob.glob(os.path.join(self.path, "agreement_*_to_*.json")))
        for fn in files:
            body = json.load(open(fn))
            if not body.get("isSuccessful") or not body.get("result"):
                continue
            res = body["result"]
            recv_meta = json.loads(res["receivingInstitution"]) if isinstance(res["receivingInstitution"], str) else res["receivingInstitution"]
            send_meta = json.loads(res["sendingInstitution"]) if isinstance(res["sendingInstitution"], str) else res["sendingInstitution"]
            ay = json.loads(res["academicYear"]) if isinstance(res["academicYear"], str) else res["academicYear"]
            recv = _inst_ref(insts.get(recv_meta["id"], recv_meta))
            send = _inst_ref(insts.get(send_meta["id"], send_meta))
            eff_from, eff_to = ay["beginDate"][:10], ay["endDate"][:10]
            ref = body.get("_url") or f"assist:{os.path.basename(fn)}"
            key = f"{ay['id']}/{send.external_key}/to/{recv.external_key}/AllDepartments"
            yield CoverageRecord(send, recv, eff_from, eff_to)
            depts = json.loads(res["articulations"]) if isinstance(res["articulations"], str) else res["articulations"]
            for dept in depts:
                for art in dept.get("articulations") or []:
                    yield from self._articulation(art, send, recv, eff_from, eff_to, key, dept.get("name"))

    def _articulation(self, art: Dict, send, recv, eff_from, eff_to, key, dept_name) -> Iterator:
        atype = art.get("type")
        if atype == "Course":
            receiving = [_course_ref(art["course"], recv)]
        elif atype == "Series" and art.get("series"):
            receiving = [_course_ref(c, recv) for c in art["series"].get("courses") or []]
        else:
            return
        for r in receiving:
            yield r
        sa = art.get("sendingArticulation") or {}
        base_ev = {"agreement_key": key, "receiving_department": dept_name, "articulation_type": atype}
        for d in sa.get("deniedCourses") or []:
            if "prefix" not in d:
                continue
            s = _course_ref(d, send)
            yield s
            for r in receiving:
                yield AssertionRecord(s, r, DENIED, 1.0, key, eff_from, eff_to, evidence=dict(base_ev, note="deniedCourses"))
        groups = sa.get("items") or []
        conj = [c.get("groupConjunction") for c in sa.get("courseGroupConjunctions") or []]
        for gi, g in enumerate(groups):
            members = [m for m in g.get("items") or [] if m.get("type") == "Course" and m.get("prefix")]
            if not members:
                continue
            joint = len(members) > 1 and (g.get("courseConjunction") or "And") == "And"
            series_joint = len(receiving) > 1
            gkey = f"{key}#{art['course']['courseIdentifierParentId'] if atype == 'Course' else 'series'}#g{gi}" if (joint or series_joint) else None
            for m in members:
                s = _course_ref(m, send)
                yield s
                for r in receiving:
                    yield AssertionRecord(
                        s, r, SATISFIES_JOINTLY if (joint or series_joint) else SATISFIES, 1.0, key, eff_from, eff_to,
                        group_key=gkey,
                        evidence=dict(base_ev, group_index=gi, group_conjunction=g.get("courseConjunction"),
                                      group_conjunctions=conj, members=len(members), receiving_courses=len(receiving)),
                    )
