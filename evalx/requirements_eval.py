"""Evaluation 1: does the requirement engine agree with the university's official degree audit?

Inputs (JSONL, one record per student)
  transcripts: {"student_id", "program", "catalog_year", "completed": ["CS 1301", ...]}
  audits:      {"student_id", "groups": [{"title": "CS Core", "satisfied": false, "remaining": ["CS 2110", ...]}, ...]}
               — the university's own statement of each requirement group's status.
Trees: {catalog_year: requirement tree} in the step-1/reqx node format.

Output: per-student agreement, and for every disagreement the catalog text next to our parse.
The release gate is 100%: `gate()` returns False on any disagreement.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class NodeStatus:
    title: str
    kind: str
    satisfied: Optional[bool]          # None = the engine cannot decide (needs_review / filter without data)
    remaining: List[str]
    reason: str
    path: List[str]


def _norm(code: str) -> str:
    parts = code.strip().upper().split()
    return " ".join(parts) if len(parts) == 2 else code.strip().upper()


def _title_key(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def evaluate(tree: Dict[str, Any], completed: Iterable[str], credits: Optional[Dict[str, float]] = None) -> List[NodeStatus]:
    """Status of every group in the tree given completed courses. Pure."""
    done = {_norm(c) for c in completed}
    credits = {(_norm(k)): v for k, v in (credits or {}).items()}
    out: List[NodeStatus] = []

    def visit(node: Dict[str, Any], path: List[str]) -> Tuple[Optional[bool], List[str], float]:
        kind = node.get("kind")
        title = node.get("title") or node.get("code") or kind
        if kind == "course":
            code = _norm(node["code"])
            ok = code in done or any(_norm(a) in done for a in node.get("aliases") or [])
            return ok, ([] if ok else [code]), (credits.get(code, 0.0) if ok else 0.0)
        if kind == "course_list":
            codes = [_norm(c) for c in node.get("courses") or []]
            have = [c for c in codes if c in done]
            need = node.get("min_count") or 1
            ok = len(have) >= need
            rem = [] if ok else [c for c in codes if c not in done]
            return ok, rem, sum(credits.get(c, 0.0) for c in have)
        if kind == "filter":
            return None, [], 0.0
        if kind == "needs_review":
            return None, [], 0.0
        # group
        results = [visit(c, path + [title]) for c in node.get("children") or []]
        op = node.get("operator") or "all"
        sat = [r[0] for r in results]
        remaining = [x for r in results for x in r[1]]
        cred = sum(r[2] for r in results if r[0])
        if any(s is None for s in sat) and op == "all":
            ok: Optional[bool] = False if any(s is False for s in sat) else None
            reason = "contains a requirement the engine cannot decide (needs_review or unresolved filter)" if ok is None else "an undecidable child plus at least one unsatisfied child"
        elif op == "all":
            ok = all(sat); reason = "all children satisfied" if ok else f"{sat.count(False)} of {len(sat)} children unsatisfied"
        elif op == "any_n":
            need = node.get("min_count") or 1
            ok = sum(1 for s in sat if s) >= need
            reason = f"{sum(1 for s in sat if s)} of {len(sat)} children satisfied (need {need})"
            if ok:
                remaining = []
        elif op == "credits":
            need = float(node.get("min_credits") or 0)
            ok = cred >= need
            reason = f"{cred:g} credits from satisfied children (need {need:g})"
            if ok:
                remaining = []
        else:
            ok, reason = None, f"unknown operator {op!r}"
        out.append(NodeStatus(title, "group", ok, sorted(set(remaining)), reason, path))
        return ok, remaining, cred

    visit(tree, [])
    return out


def _catalog_text(tree: Dict[str, Any], title: str) -> str:
    """Whatever verbatim catalog text the parse kept for this group: description / source_lines / original_text."""
    key = _title_key(title)
    for node in _walk(tree):
        if _title_key(node.get("title") or "") == key:
            bits = [node.get("description") or ""]
            bits += node.get("source_lines") or []
            for c in _walk(node):
                bits += c.get("source_lines") or []
                if c.get("original_text"):
                    bits.append("NEEDS_REVIEW: " + c["original_text"])
            txt = "\n".join(b for b in bits if b)
            return txt or "(no catalog text stored on this node)"
    return "(group not found in tree)"


def _parse_of(tree: Dict[str, Any], title: str) -> Any:
    key = _title_key(title)
    for node in _walk(tree):
        if _title_key(node.get("title") or "") == key:
            return node
    return None


def _walk(node):
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


@dataclass
class Disagreement:
    student_id: str
    group: str
    audit_satisfied: Optional[bool]
    audit_remaining: List[str]
    engine_satisfied: Optional[bool]
    engine_remaining: List[str]
    engine_reason: str
    catalog_text: str
    engine_parse: Any


@dataclass
class RequirementReport:
    students: int
    groups_compared: int
    groups_agreed: int
    per_student: Dict[str, Dict[str, Any]]
    disagreements: List[Disagreement]
    missing_trees: List[str]
    agreement: float
    release_ok: bool

    def to_dict(self):
        d = asdict(self); return d


def compare(transcripts: List[dict], audits: List[dict], trees: Dict[str, Dict[str, Any]]) -> RequirementReport:
    audit_by = {a["student_id"]: a for a in audits}
    per_student: Dict[str, Dict[str, Any]] = {}
    dis: List[Disagreement] = []
    missing: List[str] = []
    total = agreed = 0
    for t in transcripts:
        sid = t["student_id"]
        tree = trees.get(t.get("catalog_year")) or trees.get("default")
        a = audit_by.get(sid)
        if tree is None or a is None:
            missing.append(sid); continue
        statuses = {_title_key(s.title): s for s in evaluate(tree, t.get("completed", []), t.get("credits"))}
        n = ok = 0
        for g in a.get("groups", []):
            s = statuses.get(_title_key(g["title"]))
            n += 1
            audit_rem = sorted(_norm(c) for c in g.get("remaining", []))
            if s is None:
                dis.append(Disagreement(sid, g["title"], g.get("satisfied"), audit_rem, None, [], "group title not found in our tree", _catalog_text(tree, g["title"]), None))
                continue
            same = (s.satisfied == g.get("satisfied")) and (s.satisfied or set(s.remaining) == set(audit_rem))
            if same:
                ok += 1
            else:
                dis.append(Disagreement(sid, g["title"], g.get("satisfied"), audit_rem, s.satisfied, s.remaining, s.reason,
                                        _catalog_text(tree, g["title"]), _parse_of(tree, g["title"])))
        per_student[sid] = {"groups": n, "agreed": ok, "agreement": (ok / n) if n else 1.0}
        total += n; agreed += ok
    agreement = agreed / total if total else 1.0
    return RequirementReport(len(per_student), total, agreed, per_student, dis, missing, agreement, release_ok=(agreement == 1.0 and not missing))


def render(r: RequirementReport) -> str:
    L = [f"# Requirement correctness", "", f"* students: {r.students}; groups compared: {r.groups_compared}; agreed: {r.groups_agreed}; **agreement {r.agreement:.4%}**",
         f"* release gate (100% required): **{'PASS' if r.release_ok else 'BLOCKED'}**" + (f"; students without tree/audit: {', '.join(r.missing_trees)}" if r.missing_trees else ""), ""]
    if r.per_student:
        L += ["| student | groups | agreed |", "|---|---|---|"]
        L += [f"| {s} | {v['groups']} | {v['agreed']} |" for s, v in r.per_student.items() if v["agreement"] < 1.0]
    for d in r.disagreements:
        L += ["", f"## {d.student_id} — {d.group}", "",
              f"* audit: {'satisfied' if d.audit_satisfied else 'unsatisfied'}; remaining {d.audit_remaining or '—'}",
              f"* engine: {'satisfied' if d.engine_satisfied else ('undecidable' if d.engine_satisfied is None else 'unsatisfied')}; remaining {d.engine_remaining or '—'} ({d.engine_reason})",
              "", "**Catalog text**", "", "```", d.catalog_text, "```", "", "**Our parse**", "", "```json", json.dumps(d.engine_parse, indent=1)[:2500], "```"]
    return "\n".join(L) + "\n"
