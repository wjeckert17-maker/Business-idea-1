"""Synthetic term corpus with KNOWN structure, built from real section rows (times, CRNs, instructors) in
web/data/demo.json. Everything about students and grades here is synthetic and says so in `note`."""
from __future__ import annotations

import json
import random
from typing import Any, Dict, List


def build_corpus(demo_path: str, n_sections: int = 60, n_students: int = 20, seed: int = 11, leniency_slope: float = 0.8,
                 skew_group: str = None, skew: float = 0.0) -> Dict[str, Any]:
    demo = json.load(open(demo_path))
    rng = random.Random(seed)
    secs = [s for s in demo["sections"] if s.get("instructor") and s["meetings"] and s["credits"]]
    rng.shuffle(secs)
    secs = secs[:n_sections]
    inputs: Dict[str, Any] = {}
    demographics: Dict[str, Dict[str, str]] = {}
    out_secs = []
    for s in secs:
        key = f"{s['instructor']}|{s['course_code']}"
        dept = s["course_code"].split()[0]
        level = int(s["course_code"].split()[1][0]) * 1000
        if key not in inputs:
            base_gpa = max(2.0, min(3.95, rng.gauss(3.2, 0.35)))
            group = rng.choice(["A", "B"])
            demographics[key] = {"group": group}
            grades = []
            for t in range(rng.choice([2, 3, 5])):
                n = rng.randint(30, 120)
                a = int(n * max(0, min(1, (base_gpa - 2.0) / 2.0))); b = int((n - a) * 0.6); c = int((n - a - b) * 0.7); d = int((n - a - b - c) * 0.5); f = n - a - b - c - d
                grades.append({"term_date": f"{2025 - t}-05-01", "letter_counts": {"A": a, "B": b, "C": c, "D": d, "F": f}, "w_count": rng.randint(0, n // 15), "mean_gpa": round(base_gpa + rng.gauss(0, 0.05), 3)})
            true_quality = rng.gauss(0, 0.3) + (skew if (skew_group and group == skew_group) else 0.0)
            rating = max(1.0, min(5.0, 1.0 + leniency_slope * base_gpa + true_quality + rng.gauss(0, 0.05)))
            inputs[key] = {"department": dept, "level": level, "grades": grades,
                           "sentiment": [{"observed_date": "2026-03-01", "n": rng.choice([20, 60, 150]), "rating": round(rating, 3), "difficulty": round(max(1, min(5, 6.3 - base_gpa + rng.gauss(0, 0.2))), 3), "would_take_again": round(min(1, rating / 5.5), 3)}],
                           "syllabus": {"exam_weight": rng.choice([0.4, 0.6]), "attendance_required": rng.choice([True, False]), "recorded_lectures": rng.choice([True, False])}}
        out_secs.append({k: s[k] for k in ("crn", "course_code", "section_code", "title", "meetings", "instructor", "capacity", "enrolled", "credits")} | {"section_key": key})
    courses = {c: demo["courses"][c] for c in {s["course_code"] for s in out_secs}}
    # synthetic students: 3-4 mutually non-conflicting sections each
    regs = []
    for i in range(n_students):
        pool = out_secs[:]; rng.shuffle(pool)
        chosen: List[dict] = []
        for s in pool:
            if any(m["weekday"] == n["weekday"] and m["start"] < n["end"] and n["start"] < m["end"] for c in chosen for m in s["meetings"] for n in c["meetings"]):
                continue
            if s["course_code"] in {c["course_code"] for c in chosen}:
                continue
            chosen.append(s)
            if len(chosen) == rng.choice([3, 4]):
                break
        regs.append({"student_id": f"S{i:03d}", "completed": [], "crns": [c["crn"] for c in chosen], "allow_outside": True})
    return {"term": demo["term"], "note": "SYNTHETIC: real section rows from the Fall 2026 feed; grades, ratings, demographics and students are generated",
            "sections": out_secs, "scoring_inputs": inputs, "courses": courses, "requirement_tree": demo["requirement_tree"],
            "registrations": regs, "demographics": demographics}


def build_requirement_fixture(tree: Dict[str, Any], n: int = 5, seed: int = 3, corrupt_one: bool = False):
    """Transcripts + audits generated FROM the tree (so agreement is 100% by construction); optionally corrupt
    one audit group to exercise the disagreement dump."""
    from .requirements_eval import evaluate
    rng = random.Random(seed)
    codes = sorted({c["code"] for c in _walk(tree) if c.get("kind") == "course"} | {x for c in _walk(tree) if c.get("kind") == "course_list" for x in c["courses"]})
    transcripts, audits = [], []
    for i in range(n):
        done = rng.sample(codes, rng.randint(2, max(3, len(codes) // 2)))
        transcripts.append({"student_id": f"T{i:02d}", "program": tree.get("title"), "catalog_year": "2026-27", "completed": done})
        groups = [{"title": s.title, "satisfied": bool(s.satisfied), "remaining": s.remaining} for s in evaluate(tree, done) if s.title and s.satisfied is not None]
        if corrupt_one and i == 0 and groups:
            g = groups[0]; g["satisfied"] = not g["satisfied"]; g["remaining"] = [] if g["satisfied"] else ["CS 9999"]
        audits.append({"student_id": f"T{i:02d}", "groups": groups})
    return transcripts, audits


def _walk(n):
    yield n
    for c in n.get("children") or []:
        yield from _walk(c)
