"""Convert validated trees into rows for the step-1 schema (program, program_version, requirement_group,
requirement, course_list) plus a review queue. NEEDS_REVIEW becomes a requirement_group titled
'NEEDS_REVIEW' whose description is the verbatim catalog text and which has no children, so the loader
cannot mistake it for a satisfiable rule; the review queue lists every one of them."""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .validate import Report, _norm


def emit(report: Report, program_code: str, catalog_year: str) -> Dict[str, Any]:
    out = {"program": {"code": program_code, "name": report.program_title, "kind": "major"},
           "program_version": {"catalog_year": catalog_year, "total_credits": report.stated_total_units},
           "requirement_group": [], "requirement": [], "course_list": [], "review_queue": [], "confidence": {}}
    counter = {"g": 0, "r": 0, "l": 0}

    def gid():
        counter["g"] += 1; return f"g{counter['g']}"

    root = gid()
    out["requirement_group"].append({"id": root, "parent_id": None, "position": 0, "title": "Degree requirements", "operator": "all",
                                     "min_count": None, "min_credits": None, "description": None, "needs_review": False})

    def walk(node: Dict[str, Any], parent: str, pos: int, group_title: str):
        kind = node.get("kind")
        if kind == "group":
            g = gid()
            out["requirement_group"].append({"id": g, "parent_id": parent, "position": pos, "title": node.get("title"), "operator": node.get("operator"),
                                             "min_count": node.get("min_count"), "min_credits": node.get("min_credits"), "description": None, "needs_review": False})
            for i, c in enumerate(node.get("children") or []):
                walk(c, g, i, group_title)
        elif kind == "course":
            counter["r"] += 1
            out["requirement"].append({"id": f"r{counter['r']}", "group_id": parent, "position": pos, "title": node.get("title"),
                                       "course_code": _norm(node["code"]), "aliases": [_norm(a) for a in node.get("aliases") or []],
                                       "min_count": 1, "min_credits": None, "source_text": " / ".join(node.get("source_lines") or [])})
        elif kind == "course_list":
            counter["l"] += 1; counter["r"] += 1
            lid = f"l{counter['l']}"
            out["course_list"].append({"id": lid, "name": node.get("title") or f"{group_title} list {counter['l']}", "courses": [_norm(c) for c in node["courses"]]})
            out["requirement"].append({"id": f"r{counter['r']}", "group_id": parent, "position": pos, "title": node.get("title"), "course_list_id": lid,
                                       "min_count": node.get("min_count") or 1, "min_credits": node.get("min_credits"), "source_text": " / ".join(node.get("source_lines") or [])})
        elif kind == "filter":
            counter["r"] += 1
            out["requirement"].append({"id": f"r{counter['r']}", "group_id": parent, "position": pos, "title": node.get("title"), "subject_codes": node.get("subject_codes"),
                                       "min_level": node.get("min_level"), "max_level": node.get("max_level"), "min_count": node.get("min_count") or 1,
                                       "min_credits": node.get("min_credits"), "source_text": " / ".join(node.get("source_lines") or [])})
        elif kind == "needs_review":
            g = gid()
            out["requirement_group"].append({"id": g, "parent_id": parent, "position": pos, "title": "NEEDS_REVIEW", "operator": "all", "min_count": None,
                                             "min_credits": None, "description": node.get("original_text"), "needs_review": True})
            out["review_queue"].append({"group_id": g, "section": group_title, "original_text": node.get("original_text"), "reason": node.get("reason")})

    for i, gr in enumerate(report.groups):
        walk(gr.tree, root, i, gr.title)
        out["confidence"][gr.title] = {"confidence": gr.confidence, "model_confidence": gr.model_confidence, "needs_review": gr.needs_review,
                                       "reasons": gr.review_reasons, "credits": [gr.credits_min, gr.credits_max], "stated": gr.stated_units}
        if gr.needs_review and not any(q["section"] == gr.title for q in out["review_queue"]):
            out["review_queue"].append({"group_id": None, "section": gr.title, "original_text": None, "reason": "; ".join(gr.review_reasons) or "low confidence"})
    out["findings"] = [f.__dict__ for f in report.findings]
    out["ok_to_load"] = report.ok_to_load
    return out
