"""The Course table the validator checks against. Three loaders, one shape."""
from __future__ import annotations

import glob
import html as htmlmod
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from .extract import normalize_code


@dataclass
class Course:
    code: str                 # normalized 'ECS 36A'
    title: Optional[str]
    units_min: Optional[float]
    units_max: Optional[float]
    aliases: Tuple[str, ...] = ()   # cross-listed codes that name the same course


class CourseTable:
    def __init__(self, courses: Iterable[Course]):
        self.by_code: Dict[str, Course] = {}
        for c in courses:
            self.by_code[c.code] = c
            for a in c.aliases:
                self.by_code.setdefault(a, c)

    def get(self, code: str) -> Optional[Course]:
        return self.by_code.get(code)

    def __contains__(self, code: str) -> bool:
        return code in self.by_code

    def __len__(self) -> int:
        return len({id(c) for c in self.by_code.values()})

    # -- loaders ----------------------------------------------------------
    @classmethod
    def from_json(cls, path: str) -> "CourseTable":
        rows = json.load(open(path))
        return cls(Course(normalize_code(*r["code"].split(" ", 1)), r.get("title"), r.get("units_min"), r.get("units_max"),
                          tuple(normalize_code(*a.split(" ", 1)) for a in r.get("aliases", []))) for r in rows)

    @classmethod
    def from_postgres(cls, dsn: str, institution_slug: str, catalog_year_label: Optional[str] = None) -> "CourseTable":
        """Reads course_catalog_entry from the step-1 schema."""
        import psycopg
        sql = """SELECT e.subject_code, e.course_number, e.title, e.credits_min, e.credits_max, cy.label
                 FROM course_catalog_entry e JOIN catalog_year cy USING (catalog_year_id) JOIN institution i ON i.institution_id = cy.institution_id
                 WHERE i.slug = %s""" + (" AND cy.label = %s" if catalog_year_label else "")
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(sql, (institution_slug, catalog_year_label) if catalog_year_label else (institution_slug,)).fetchall()
        return cls(Course(normalize_code(s, n), t, float(lo) if lo is not None else None, float(hi) if hi is not None else None) for s, n, t, lo, hi, _ in rows)

    @classmethod
    def from_courseleaf_dir(cls, directory: str) -> "CourseTable":
        """Parses CourseLeaf courses-by-subject pages (catalog.*.edu/courses-subject-code/<subj>/)."""
        courses = []
        for fn in sorted(glob.glob(os.path.join(directory, "*.html"))):
            h = open(fn, encoding="utf-8", errors="replace").read()
            for block in re.findall(r'<div class="courseblock">(.*?)</h3>', h, re.S):
                code = re.search(r'detail-code[^>]*><b>([^<]+)</b>', block)
                title = re.search(r'detail-title[^>]*><b>[—–-]?\s*([^<]+)</b>', block)
                units = re.search(r'detail-hours_html[^>]*><b>\(([^)]+)\)</b>', block)
                if not code:
                    continue
                raw = htmlmod.unescape(code.group(1)).strip()
                parts = raw.split(" ", 1)
                if len(parts) != 2:
                    continue
                lo, hi = _units(units.group(1) if units else "")
                courses.append(Course(normalize_code(parts[0], parts[1]), htmlmod.unescape(title.group(1)).strip() if title else None, lo, hi))
        return cls(courses)

    def to_json(self, path: str) -> None:
        json.dump([{"code": c.code, "title": c.title, "units_min": c.units_min, "units_max": c.units_max, "aliases": list(c.aliases)}
                   for c in {id(c): c for c in self.by_code.values()}.values()], open(path, "w"), indent=1)


def _units(s: str) -> Tuple[Optional[float], Optional[float]]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return (float(m.group(1)), float(m.group(1))) if m else (None, None)
