"""Mention adapters: each source becomes a stream of MentionRecord. Adding a source = one class."""
from __future__ import annotations

import csv
import json
import os
import re
from abc import ABC, abstractmethod
from typing import ClassVar, Dict, Iterator, Type

from ..models import InstitutionRef, MentionRecord


class MentionAdapter(ABC):
    code: ClassVar[str]
    kind: ClassVar[str]
    base_trust: ClassVar[float]

    def __init__(self, path: str, institution: InstitutionRef, **options):
        self.path, self.institution, self.options = path, institution, options

    @abstractmethod
    def records(self) -> Iterator[MentionRecord]:
        ...


_REG: Dict[str, Type[MentionAdapter]] = {}


def register(cls):
    _REG[cls.code] = cls
    return cls


def get_mention_adapter(code: str) -> Type[MentionAdapter]:
    if code not in _REG:
        raise KeyError(f"unknown mention source {code!r}; known: {sorted(_REG)}")
    return _REG[code]


def term_label_from_banner(code: str) -> str:
    """'202608' -> '2026-fall' (Banner YYYYMM with MM in {02,05,08} at most schools)."""
    m = re.fullmatch(r"(\d{4})(\d{2})", code or "")
    if not m:
        return code
    season = {"08": "fall", "09": "fall", "10": "fall", "02": "spring", "01": "spring", "05": "summer", "06": "summer"}.get(m.group(2), m.group(2))
    return f"{m.group(1)}-{season}"


@register
class SisSectionsAdapter(MentionAdapter):
    """JSONL of course_ingest SectionRecord dumps (one file per term, named sections_<termcode>.jsonl)."""
    code = "sis"
    kind = "sis"
    base_trust = 1.0

    def records(self) -> Iterator[MentionRecord]:
        files = [self.path] if os.path.isfile(self.path) else sorted(
            os.path.join(self.path, f) for f in os.listdir(self.path) if f.endswith(".jsonl"))
        for fn in files:
            m = re.search(r"sections_(\d+)\.jsonl$", fn)
            term = term_label_from_banner(m.group(1)) if m else self.options.get("term", "unknown")
            with open(fn) as fh:
                for line in fh:
                    rec = json.loads(line)
                    course = f"{rec['subject_code']} {rec['course_number']}"
                    for ins in rec.get("instructors") or []:
                        yield MentionRecord(
                            source_code="sis", raw_name=ins["display_name"], institution=self.institution,
                            email=ins.get("email"), external_id=ins.get("external_id"), department=rec["subject_code"],
                            courses=[course], terms=[term], role=ins.get("role"),
                            extra={"crn": rec["crn"], "section": rec.get("section_code")},
                            source_ref=f"sis:{os.path.basename(fn)}:crn:{rec['crn']}")


@register
class GradeFeedAdapter(MentionAdapter):
    """CSV/JSONL rows: instructor (raw string), subject, number, term, optional department."""
    code = "grade_feed"
    kind = "grade_feed"
    base_trust = 0.8

    def records(self) -> Iterator[MentionRecord]:
        rows = _rows(self.path)
        for i, r in enumerate(rows, 1):
            name = (r.get("instructor") or r.get("instructor_name") or "").strip()
            if not name:
                continue
            course = f"{(r.get('subject') or '').strip()} {(r.get('number') or r.get('course_number') or '').strip()}".strip()
            yield MentionRecord(source_code="grade_feed", raw_name=name, institution=self.institution,
                                department=r.get("department") or (r.get("subject") or "").strip() or None,
                                courses=[course] if course else [], terms=[r["term"]] if r.get("term") else [],
                                extra={k: v for k, v in r.items() if k not in ("instructor", "instructor_name")},
                                source_ref=f"grade_feed:{os.path.basename(self.path)}:row:{i}")


@register
class SyllabusAdapter(MentionAdapter):
    """JSONL rows produced by a PDF-extraction step: name, email?, course, term, file."""
    code = "syllabus"
    kind = "syllabus"
    base_trust = 0.7

    def records(self) -> Iterator[MentionRecord]:
        for i, r in enumerate(_rows(self.path), 1):
            if not r.get("name"):
                continue
            yield MentionRecord(source_code="syllabus", raw_name=r["name"], institution=self.institution, email=r.get("email"),
                                department=r.get("department"), courses=[r["course"]] if r.get("course") else [],
                                terms=[r["term"]] if r.get("term") else [], extra={"file": r.get("file")},
                                source_ref=f"syllabus:{r.get('file') or os.path.basename(self.path)}:{i}")


@register
class RatingSiteAdapter(MentionAdapter):
    """JSONL rows from a rating-site export: name, department, school, courses (list), profile_url."""
    code = "rating_site"
    kind = "rating_site"
    base_trust = 0.5

    def records(self) -> Iterator[MentionRecord]:
        for i, r in enumerate(_rows(self.path), 1):
            if not r.get("name"):
                continue
            yield MentionRecord(source_code="rating_site", raw_name=r["name"], institution=self.institution,
                                department=r.get("department"), courses=list(r.get("courses") or []), terms=list(r.get("terms") or []),
                                extra={"profile_url": r.get("profile_url"), "school": r.get("school")},
                                source_ref=r.get("profile_url") or f"rating_site:{os.path.basename(self.path)}:{i}")


def _rows(path: str):
    if path.endswith(".csv"):
        with open(path, newline="") as fh:
            return list(csv.DictReader(fh))
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]
