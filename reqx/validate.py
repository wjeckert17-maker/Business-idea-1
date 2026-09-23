"""Step 3 and 4: validate the model's tree against the source text, the Course table, credit totals and
satisfiability; compute a confidence per group; route anything doubtful to human review.

Severities
  error    the tree cannot be loaded as-is (fabricated code, unknown course, unsatisfiable group, schema violation)
  warning  loadable but a human should look (credit total off, low confidence, needs_review present)
  info     bookkeeping (computed credit ranges)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple

import jsonschema

from .courses import CourseTable
from .extract import Outline, Section, codes_in, normalize_code
from .prompt import OUTPUT_SCHEMA

REVIEW_THRESHOLD = 0.8
REVIEW_PHRASES = ("advisor", "adviser", "approved substitute", "approval", "consultation", "petition", "recommended",
                  "may be substituted", "see the department", "current list", "at the discretion", "or equivalent", "as approved")


@dataclass
class Finding:
    group: str
    severity: str
    code: str
    message: str
    node_path: str = ""


@dataclass
class GroupResult:
    title: str
    model_confidence: float
    confidence: float
    needs_review: bool
    review_reasons: List[str]
    credits_min: Optional[float]
    credits_max: Optional[float]
    stated_units: Optional[str]
    tree: Dict[str, Any]


@dataclass
class Report:
    program_title: str
    stated_total_units: Optional[str]
    computed_total_min: Optional[float]
    computed_total_max: Optional[float]
    groups: List[GroupResult]
    findings: List[Finding]
    ok_to_load: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"program_title": self.program_title, "stated_total_units": self.stated_total_units,
                "computed_total_min": self.computed_total_min, "computed_total_max": self.computed_total_max,
                "ok_to_load": self.ok_to_load, "findings": [asdict(f) for f in self.findings],
                "groups": [asdict(g) for g in self.groups]}


# ---------------------------------------------------------------------------
def _norm(code: str) -> Optional[str]:
    m = re.match(r"^\s*([A-Z]{2,4})\s*0*(\d{1,4}[A-Z]{0,3})\s*$", code.upper())
    return normalize_code(m.group(1), m.group(2)) if m else None


def _units_range(s: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    if not s:
        return None, None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)\s*$", str(s))
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"^\s*(\d+(?:\.\d+)?)", str(s))
    return (float(m.group(1)), float(m.group(1))) if m else (None, None)


def _walk(node: Dict[str, Any], path: str = "group"):
    yield path, node
    for i, c in enumerate(node.get("children") or []):
        yield from _walk(c, f"{path}.children[{i}]")


class Validator:
    def __init__(self, courses: CourseTable, outline: Outline):
        self.courses, self.outline = courses, outline

    # -- entry point ------------------------------------------------------
    def validate(self, section: Section, model_out: Dict[str, Any]) -> Tuple[GroupResult, List[Finding]]:
        F: List[Finding] = []
        g = section.title
        try:
            jsonschema.validate(model_out, OUTPUT_SCHEMA)
        except jsonschema.ValidationError as e:
            F.append(Finding(g, "error", "schema", f"model output violates schema: {e.message[:200]}", "/".join(str(p) for p in e.path)))
            return GroupResult(g, 0.0, 0.0, True, ["schema violation"], None, None, None, model_out), F
        tree = model_out["group"]
        source_text = section.all_text()
        source_codes: Set[str] = set()
        for l in _lines(section):
            source_codes.update(l.codes)
        source_codes.update(codes_in(source_text))
        reasons: List[str] = []
        penalty = 1.0

        for path, node in _walk(tree):
            kind = node.get("kind")
            # 1. fabrication check: every code the model emitted must be in this section's text
            for raw in ([node.get("code")] if node.get("code") else []) + list(node.get("aliases") or []) + list(node.get("courses") or []):
                code = _norm(raw)
                if code is None:
                    F.append(Finding(g, "error", "bad_code", f"unparseable course code {raw!r}", path)); penalty *= 0.5; continue
                if code not in source_codes:
                    F.append(Finding(g, "error", "fabricated_code", f"{raw!r} does not appear in the section text", path))
                    reasons.append(f"code {raw} not in source"); penalty *= 0.3
                    continue
                # 2. existence in the Course table
                if code not in self.courses and not any(_norm(a) in self.courses for a in (node.get("aliases") or []) if _norm(a)):
                    F.append(Finding(g, "error", "unknown_course", f"{raw!r} is not in the Course table", path))
                    reasons.append(f"{raw} not in Course table"); penalty *= 0.7
            # 3. structural sanity
            if kind == "group":
                kids = node.get("children") or []
                op = node.get("operator")
                if not kids:
                    F.append(Finding(g, "error", "empty_group", f"group {node.get('title')!r} has no children", path)); penalty *= 0.5
                if op == "any_n":
                    n = node.get("min_count")
                    if not n or n < 1:
                        F.append(Finding(g, "error", "bad_min_count", f"any_n group {node.get('title')!r} needs min_count >= 1", path)); penalty *= 0.5
                    elif n > len(kids):
                        F.append(Finding(g, "error", "unsatisfiable", f"group {node.get('title')!r} asks for {n} of {len(kids)} children", path)); penalty *= 0.3
                if op == "credits":
                    lo, hi = self.credit_range(node)
                    mc = node.get("min_credits")
                    if not mc:
                        F.append(Finding(g, "error", "bad_min_credits", f"credits group {node.get('title')!r} needs min_credits", path)); penalty *= 0.5
                    elif hi is not None and mc > hi:
                        F.append(Finding(g, "error", "unsatisfiable", f"group {node.get('title')!r} asks for {mc} credits but its children can supply at most {hi}", path)); penalty *= 0.3
                if op not in ("all", "any_n", "credits"):
                    F.append(Finding(g, "error", "bad_operator", f"group {node.get('title')!r} has operator {op!r}", path)); penalty *= 0.5
            elif kind == "course" and not node.get("code"):
                F.append(Finding(g, "error", "missing_code", "course node without a code", path)); penalty *= 0.5
            elif kind == "course_list" and not node.get("courses"):
                F.append(Finding(g, "error", "empty_list", "course_list without courses", path)); penalty *= 0.5
            elif kind == "filter" and not node.get("subject_codes"):
                F.append(Finding(g, "warning", "vague_filter", "filter without subject codes; should this be needs_review?", path)); penalty *= 0.8
            elif kind == "needs_review":
                txt = (node.get("original_text") or "").strip()
                if not txt:
                    F.append(Finding(g, "error", "review_without_text", "needs_review node must carry the original text", path)); penalty *= 0.5
                elif _squash(txt) not in _squash(source_text):
                    F.append(Finding(g, "error", "review_text_not_verbatim", "needs_review original_text is not verbatim from the section", path)); penalty *= 0.5
                reasons.append(f"needs_review: {txt[:80]}")
                penalty = min(penalty, 0.8)
            # 4. units copied, not invented
            if node.get("units") and _squash(str(node["units"])) not in _squash(source_text):
                F.append(Finding(g, "warning", "units_not_in_source", f"units {node['units']!r} not found in section text", path)); penalty *= 0.8

        # 5. ambiguity phrases the model should have escalated
        review_nodes = [n for _, n in _walk(tree) if n.get("kind") == "needs_review"]
        for phrase in REVIEW_PHRASES:
            if phrase in source_text.lower() and not any(phrase in (n.get("original_text") or "").lower() for n in review_nodes):
                F.append(Finding(g, "warning", "unescalated_ambiguity", f"section mentions {phrase!r} but no needs_review node quotes it"))
                reasons.append(f"'{phrase}' not escalated"); penalty *= 0.7

        # 6. credit total vs stated subtotal for this section
        lo, hi = self.credit_range(tree)
        stated = next((l.units for l in section.lines if l.kind == "subtotal"), None) or next((l.units for l in _lines(section) if l.kind == "subtotal"), None)
        if stated:
            slo, shi = _units_range(stated)
            if slo is not None and lo is not None and hi is not None and (shi < lo or slo > hi):
                F.append(Finding(g, "error", "credit_mismatch", f"section subtotal states {stated} but the tree yields {lo:g}-{hi:g}"))
                reasons.append("subtotal mismatch"); penalty *= 0.5
            else:
                F.append(Finding(g, "info", "credits", f"section subtotal {stated}; tree yields {lo}-{hi}"))

        model_conf = float(model_out.get("confidence", 0.0))
        conf = round(max(0.0, min(1.0, model_conf * penalty)), 3)
        needs_review = conf < REVIEW_THRESHOLD or any(f.severity == "error" for f in F) or bool(review_nodes)
        if conf < REVIEW_THRESHOLD and "low confidence" not in reasons:
            reasons.append(f"confidence {conf} < {REVIEW_THRESHOLD}")
        return GroupResult(g, model_conf, conf, needs_review, reasons, lo, hi, stated, tree), F

    # -- credits -------------------------------------------------------------
    def credit_range(self, node: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        """(min, max) units a student could earn satisfying this node; None when unknown."""
        kind = node.get("kind")
        if kind == "course":
            lo, hi = _units_range(node.get("units"))
            if lo is None:
                c = self.courses.get(_norm(node.get("code") or "") or "")
                if c:
                    lo, hi = c.units_min, c.units_max
            return lo, hi
        if kind == "course_list":
            ranges = [self._course_units(c) for c in node.get("courses") or []]
            ranges = [r for r in ranges if r[0] is not None]
            n = node.get("min_count") or 1
            if node.get("min_credits"):
                return float(node["min_credits"]), float(node["min_credits"])
            if len(ranges) < n:
                return None, None
            los = sorted(r[0] for r in ranges); his = sorted((r[1] for r in ranges), reverse=True)
            return sum(los[:n]), sum(his[:n])
        if kind == "filter":
            if node.get("min_credits"):
                return float(node["min_credits"]), float(node["min_credits"])
            return None, None
        if kind == "needs_review":
            lo, hi = _units_range(node.get("units"))
            return lo, hi
        kids = [self.credit_range(c) for c in node.get("children") or []]
        op = node.get("operator")
        if op == "all":
            known_lo = [k[0] for k in kids if k[0] is not None]
            lo = sum(known_lo) if known_lo else None
            hi = sum(k[1] for k in kids) if kids and all(k[1] is not None for k in kids) else None
            return lo, hi
        if op == "any_n":
            n = node.get("min_count") or 1
            known = [k for k in kids if k[0] is not None]
            if len(known) < n:
                return None, None
            los = sorted(k[0] for k in known)
            his = sorted((k[1] for k in known if k[1] is not None), reverse=True)
            return sum(los[:n]), (sum(his[:n]) if len(his) >= n else None)
        if op == "credits":
            mc = node.get("min_credits")
            known = [k for k in kids if k[1] is not None]
            return (float(mc) if mc else None), (sum(k[1] for k in known) if known else None)
        return None, None

    def _course_units(self, code: str):
        c = self.courses.get(_norm(code) or "")
        return (c.units_min, c.units_max) if c else (None, None)


def _lines(sec: Section):
    for l in sec.lines:
        yield l
    for c in sec.children:
        yield from _lines(c)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def finish(outline: Outline, results: List[GroupResult], findings: List[Finding]) -> Report:
    """Degree-level credit check across all groups."""
    los = [g.credits_min for g in results]; his = [g.credits_max for g in results]
    tot_lo = sum(x for x in los if x is not None) if any(x is not None for x in los) else None
    tot_hi = sum(x for x in his if x is not None) if all(x is not None for x in his) and his else None
    stated = outline.stated_total_units
    if stated and tot_lo is not None:
        slo, shi = _units_range(stated)
        if tot_hi is not None and (shi < tot_lo or slo > tot_hi):
            findings.append(Finding("(degree)", "error", "degree_total_mismatch", f"catalog states {stated} units; tree yields {tot_lo:g}-{tot_hi:g}"))
        elif tot_hi is None and slo > tot_lo:
            findings.append(Finding("(degree)", "warning", "degree_total_unknown", f"catalog states {stated}; tree yields at least {tot_lo:g} with some groups of unknown size"))
        else:
            findings.append(Finding("(degree)", "info", "degree_total", f"catalog states {stated}; tree yields {tot_lo}-{tot_hi}"))
    ok = not any(f.severity == "error" for f in findings)
    return Report(outline.program_title, stated, tot_lo, tot_hi, results, findings, ok)
