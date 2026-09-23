"""Texas Common Course Numbering System matrix (.xls export, sheet 'Results').

Layout: row 0 = FICE codes, row 1 = CEEB codes, row 2 = headers ('Common Course Title', 'Common' prefix,
'Common' number, then one column per institution). Body rows: common title/prefix/number, then each
institution's local course number(s), e.g. 'ACCT 210', 'ACC 2303/2000', 'ACCT 0000' (0000 = placeholder).

Each local course is member_of the TCCNS hub (prefix, number). Local titles are not in the matrix, so
these nodes carry the common title as a fallback title with attrs.title_source='tccns_common'.
Coverage: all ordered pairs of institutions in the matrix (the matrix is exhaustive for common numbers).
Effective range: the matrix year (Fall YYYY -> Fall YYYY+1).
"""
from __future__ import annotations

import re
from typing import Iterator, List

from ..models import AssertionRecord, CourseRef, CoverageRecord, InstitutionRef, MEMBER_OF
from .base import GroundTruthAdapter, register

_LOCAL = re.compile(r"([A-Z]{2,5})\s*[- ]?\s*(\d{3,4}[A-Z]?)")


def split_local(cell: str) -> List[tuple]:
    """'ACC 2303/2000' -> [('ACC','2303'), ('ACC','2000')]; 'ACCT 0000' -> []."""
    out, last_prefix = [], None
    for part in re.split(r"[/,;]| or | and ", cell.strip()):
        part = part.strip()
        if not part:
            continue
        m = _LOCAL.match(part)
        if m:
            last_prefix, num = m.group(1), m.group(2)
        elif re.fullmatch(r"\d{3,4}[A-Z]?", part) and last_prefix:
            num = part
        else:
            continue
        if set(num) == {"0"}:
            continue
        out.append((last_prefix, num))
    return out


@register
class TccnsAdapter(GroundTruthAdapter):
    code = "tccns"
    name = "Texas Common Course Numbering System"
    kind = "common_numbering"
    base_trust = 0.98

    def __init__(self, path: str, year: int = 2025, **options):
        super().__init__(path, **options)
        self.year = year

    def records(self) -> Iterator:
        import xlrd
        wb = xlrd.open_workbook(self.path, ignore_workbook_corruption=True)
        sh = wb.sheet_by_name("Results")
        fice = [str(sh.cell_value(0, c)).replace(".0", "") for c in range(sh.ncols)]
        names = [str(sh.cell_value(2, c)).strip() for c in range(sh.ncols)]
        insts = {}
        for c in range(3, sh.ncols):
            if names[c]:
                insts[c] = InstitutionRef("tccns", fice[c] or names[c], names[c], state="TX")
        eff_from, eff_to = f"{self.year}-08-01", f"{self.year + 1}-08-01"
        for r in range(3, sh.nrows):
            title = str(sh.cell_value(r, 0)).strip()
            prefix = str(sh.cell_value(r, 1)).strip()
            number = str(sh.cell_value(r, 2)).replace(".0", "").strip()
            if not prefix or not number:
                continue
            hub = CourseRef(subject=prefix, number=number, system_code="tccns", title=title or None)
            yield hub
            for c, inst in insts.items():
                cell = str(sh.cell_value(r, c)).strip()
                if not cell:
                    continue
                for lp, ln in split_local(cell):
                    course = CourseRef(subject=lp, number=ln, institution=inst, title=title or None,
                                       effective_from=eff_from, effective_to=eff_to,
                                       attrs={"title_source": "tccns_common", "matrix_cell": cell})
                    yield course
                    yield AssertionRecord(course, hub, MEMBER_OF, 1.0, f"tccns:matrix:{self.year}:row{r + 1}:col{c + 1}",
                                          eff_from, eff_to, evidence={"cell": cell})
        ilist = list(insts.values())
        for a in ilist:
            for b in ilist:
                if a.external_key != b.external_key:
                    yield CoverageRecord(a, b, eff_from, eff_to)
