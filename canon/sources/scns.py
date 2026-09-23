"""Florida Statewide Course Numbering System flat file (crslist.txt, fixed width, 411 chars).

Rule (Fla. Stat. 1007.24): courses with the same prefix and the same last three digits (century,
decade, unit) plus lab code are the same statewide course; the first digit is the institution's
level. So every institution course is a member_of the hub (prefix, cdu, lab). Course-to-course
equivalence is derived through the hub at resolution time.

Effective range: DT_EFFECTIVE .. DT_DISCONTINUED (MMDDYYYY). Status A/D/P is kept on the version.
Coverage: every ordered pair of SCNS institutions — the system is exhaustive by construction:
two SCNS courses with different statewide numbers are, by definition, not the same course.
Caveat recorded in notes: transferability across sectors also depends on CD_TRANSFERABLE and on
the receiving institution offering the number; we record membership, not a transfer guarantee.
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional

from ..models import AssertionRecord, CourseRef, CoverageRecord, InstitutionRef, MEMBER_OF
from ..text import parse_units
from .base import GroundTruthAdapter, register

FIELDS = [("ID_INSTITUTION", 7), ("ID_DISCIPLINE", 3), ("ID_PREFIX", 3), ("CD_LEVEL", 1), ("ID_CENTURY", 1), ("ID_DECADE", 1),
          ("ID_UNIT", 1), ("CD_LAB", 1), ("ID_COURSE", 7), ("IN_HONORS", 1), ("CD_COURSE_STATUS", 3), ("IC_DS_TITLE", 150),
          ("DS_COURSE_CREDIT", 6), ("DS_CLOCK_HOURS", 6), ("CD_CREDIT_TYPE", 3), ("DT_EFFECTIVE", 8), ("DT_DISCONTINUED", 8),
          ("DT_CREATED", 8), ("DT_CHANGED", 8), ("IN_RULE", 1), ("IN_RULE_WRITING", 1), ("GE_COM", 1), ("GE_HUM", 1), ("GE_MATH", 1),
          ("GE_NATSCI", 1), ("GE_SOCSCI", 1), ("CD_CREDENTIAL", 3), ("DT_CREDENTIAL", 8), ("ID_COURSE_OLD", 7), ("CS_DS_TITLE", 150),
          ("IN_COMMON_PREREQ", 1), ("IN_DUAL_ENROLL", 1), ("CD_HS_CREDIT", 4), ("CD_TRANSFERABLE", 3), ("IN_WBL", 1)]


def _date(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not re.fullmatch(r"\d{8}", s) or s == "00000000":
        return None
    return f"{s[4:8]}-{s[0:2]}-{s[2:4]}"


def parse_line(line: str) -> Dict[str, str]:
    out, pos = {}, 0
    for name, w in FIELDS:
        out[name] = line[pos:pos + w].strip()
        pos += w
    return out


@register
class ScnsAdapter(GroundTruthAdapter):
    code = "scns"
    name = "Florida Statewide Course Numbering System"
    kind = "common_numbering"
    base_trust = 0.98
    notes = "Statutory common numbering; membership implies equivalence for transfer among SCNS institutions."

    def __init__(self, path: str, institutions: Dict[str, str] = None, include_discontinued: bool = True, **options):
        super().__init__(path, **options)
        self.institutions = institutions or {}
        self.include_discontinued = include_discontinued

    def institution_ref(self, inst_id: str) -> InstitutionRef:
        name = self.institutions.get(inst_id) or self.institutions.get(inst_id.lstrip("0")) or f"SCNS institution {inst_id.lstrip('0')}"
        return InstitutionRef("scns", inst_id.lstrip("0") or "0", name, state="FL")

    def records(self) -> Iterator:
        seen_inst: Dict[str, InstitutionRef] = {}
        with open(self.path, encoding="latin-1") as fh:
            for lineno, line in enumerate(fh, 1):
                r = parse_line(line.rstrip("\r\n"))
                if not r["ID_PREFIX"] or not r["ID_CENTURY"]:
                    continue
                if r["CD_COURSE_STATUS"] == "D" and not self.include_discontinued:
                    continue
                inst = seen_inst.get(r["ID_INSTITUTION"])
                if inst is None:
                    inst = seen_inst[r["ID_INSTITUTION"]] = self.institution_ref(r["ID_INSTITUTION"])
                cdu = r["ID_CENTURY"] + r["ID_DECADE"] + r["ID_UNIT"]
                number = r["CD_LEVEL"] + cdu + r["CD_LAB"]
                lo, hi = parse_units(r["DS_COURSE_CREDIT"])
                eff_from, eff_to = _date(r["DT_EFFECTIVE"]), _date(r["DT_DISCONTINUED"])
                course = CourseRef(
                    subject=r["ID_PREFIX"], number=number, institution=inst,
                    title=r["IC_DS_TITLE"].title() or None, units_min=lo, units_max=hi,
                    effective_from=eff_from, effective_to=eff_to, status={"A": "active", "D": "discontinued", "P": "pending"}.get(r["CD_COURSE_STATUS"]),
                    attrs={"scns_course_id": r["ID_COURSE"], "honors": r["IN_HONORS"] == "Y", "credit_type": r["CD_CREDIT_TYPE"],
                           "transferable": r["CD_TRANSFERABLE"], "statewide_title": r["CS_DS_TITLE"].title(), "discipline": r["ID_DISCIPLINE"]},
                )
                hub = CourseRef(subject=r["ID_PREFIX"], number=cdu + r["CD_LAB"], system_code="scns",
                                title=r["CS_DS_TITLE"].title() or None, attrs={"discipline": r["ID_DISCIPLINE"]})
                yield course
                yield hub
                yield AssertionRecord(course, hub, MEMBER_OF, 1.0, f"scns:crslist.txt:{lineno}", eff_from, eff_to,
                                      evidence={"status": r["CD_COURSE_STATUS"], "transferable": r["CD_TRANSFERABLE"], "level": r["CD_LEVEL"]})
        insts = list(seen_inst.values())
        for a in insts:
            for b in insts:
                if a.external_key != b.external_key:
                    yield CoverageRecord(a, b, None, None)


def institutions_from_dictionary(doc_text: str) -> Dict[str, str]:
    """Parse the 'ID_INSTITUTION' value table out of the data dictionary text (id / abbr / name triples)."""
    out, lines = {}, [l.strip() for l in doc_text.splitlines() if l.strip()]
    i = 0
    while i < len(lines) - 2:
        if re.fullmatch(r"\d{1,7}", lines[i]) and re.fullmatch(r"[A-Z\-]{1,6}", lines[i + 1]):
            out[lines[i]] = lines[i + 2]
            i += 3
        else:
            i += 1
    return out
