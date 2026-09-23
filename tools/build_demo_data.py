"""Build web/data/demo.json: a real term of Georgia Tech sections (CS, MATH, PHYS, ENGL, HIST, PSYC) from the
course_ingest dump, a hand-written requirement tree with catalog citations, and DEMO grade / rating data
produced by the scoring service from synthetic inputs (clearly labelled as demo in the UI)."""
from __future__ import annotations

import html
import json
import random
import sys
from collections import defaultdict
from datetime import date, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scoring import GradeObservation, ScoringConfig, SectionInput, SentimentObservation, SyllabusFeatures, score_corpus  # noqa: E402

SUBJECTS = {"CS", "MATH", "PHYS", "ENGL", "HIST", "PSYC", "ECON", "LMC"}
DUMP = sys.argv[1]
OUT = Path(sys.argv[2])
rng = random.Random(7)


def minutes(t: str) -> int:
    h, m = t.split(":")[:2]
    return int(h) * 60 + int(m)


sections, courses = [], {}
by_instructor = defaultdict(list)
for line in open(DUMP):
    r = json.loads(line)
    if r["subject_code"] not in SUBJECTS or r["component"] not in ("lecture", "seminar", "studio"):
        continue
    if not r["credits_min"]:
        continue          # zero-credit paired components (labs/recitations billed with the lecture) are not standalone courses
    meetings = []
    for m in r["meetings"]:
        if m["time_tba"] or not m["days"] or not m["start_time"]:
            continue
        for d in m["days"]:
            meetings.append({"weekday": d, "start": minutes(m["start_time"]), "end": minutes(m["end_time"])})
    if not meetings:
        continue
    code = f"{r['subject_code']} {r['course_number']}"
    ins = r["instructors"][0]["display_name"] if r["instructors"] else None
    sec = {"section_id": r["crn"], "crn": r["crn"], "course_code": code, "section_code": r["section_code"], "title": html.unescape(r["title"]),
           "meetings": meetings, "instructor": ins, "capacity": r["capacity"], "enrolled": r["enrolled"],
           "credits": r["credits_min"], "modality": r["modality"]}
    sections.append(sec)
    courses.setdefault(code, {"code": code, "title": html.unescape(r["title"]), "credits": r["credits_min"] or 3, "prereqs": []})
    by_instructor[(ins, code)].append(sec)

# prerequisites (illustrative, hand-set for the demo's CS/MATH chain)
PREREQ = {"CS 1331": [["CS 1301"], ["CS 1315"]], "CS 1332": [["CS 1331"]], "CS 2110": [["CS 1331"]], "CS 2340": [["CS 1332"]],
          "CS 3510": [["CS 1332", "MATH 2550"], ["CS 1332", "MATH 2551"]], "CS 3600": [["CS 1332"]], "MATH 1552": [["MATH 1551"], ["MATH 1501"]],
          "MATH 1554": [["MATH 1552"]], "MATH 2550": [["MATH 1552"]], "MATH 2551": [["MATH 1552"]], "MATH 3012": [["MATH 1554"]],
          "PHYS 2212": [["PHYS 2211"]], "CS 2050": [["CS 1301"], ["CS 1315"], ["CS 1331"]]}
for code, pr in PREREQ.items():
    if code in courses:
        courses[code]["prereqs"] = pr

# DEMO grade + rating data per (instructor, course), scored by the real scoring service
dept_bias = {"CS": 0.0, "MATH": -0.35, "PHYS": -0.3, "ENGL": 0.4, "HIST": 0.3, "PSYC": 0.25, "ECON": -0.1, "LMC": 0.35}
inputs, meta = [], {}
for (ins, code), secs in by_instructor.items():
    dept = code.split()[0]
    level = int(code.split()[1][0]) * 1000
    base_gpa = max(2.0, min(3.95, rng.gauss(3.2 + dept_bias[dept], 0.3)))
    hist = []
    n_terms = rng.choice([0, 1, 2, 3, 5, 8])
    for t in range(n_terms):
        n = rng.randint(15, 180)
        a = int(n * max(0, min(1, (base_gpa - 2.0) / 2.0)) * rng.uniform(0.8, 1.0)); b = int((n - a) * 0.55); c = int((n - a - b) * 0.6)
        d = int((n - a - b - c) * 0.5); f = n - a - b - c - d
        hist.append(GradeObservation(date(2026 - t, 5, 1), {"A": a, "B": b, "C": c, "D": d, "F": f}, w_count=rng.randint(0, n // 12), mean_gpa=None))
    sent = []
    if rng.random() < 0.7:
        n = rng.choice([2, 5, 12, 40, 120])
        rating = max(1.0, min(5.0, 1.0 + 0.8 * base_gpa + rng.gauss(0, 0.4)))
        sent.append(SentimentObservation(date(2026, 3, 1), n, rating=rating, difficulty=max(1.0, min(5.0, 6.5 - base_gpa + rng.gauss(0, 0.4))), would_take_again=max(0.0, min(1.0, rating / 5.5))))
    syl = SyllabusFeatures(exam_weight=rng.choice([None, 0.3, 0.5, 0.7]), attendance_required=rng.choice([None, True, False]), recorded_lectures=rng.choice([None, True, False]))
    sid = f"{ins}|{code}"
    inputs.append(SectionInput(sid, dept, level, hist, sent, syl))
    g_n = sum(h.graded_n() + h.w_count for h in hist)
    meta[sid] = {"grade_n": g_n, "grade_terms": len(hist), "w_rate": (sum(h.w_count for h in hist) / g_n) if g_n else None,
                 "mean_gpa": (sum((h.mean_gpa or sum({"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}[k] * v for k, v in h.letter_counts.items()) / max(1, h.graded_n())) * h.graded_n() for h in hist) / max(1, sum(h.graded_n() for h in hist))) if hist else None,
                 "rating_n": sum(s.n for s in sent), "rating": sent[0].rating if sent else None}
scores, model = score_corpus(inputs, ScoringConfig(as_of=date(2026, 8, 1)))
for (ins, code), secs in by_instructor.items():
    sid = f"{ins}|{code}"
    sc = scores[sid]
    low = [c.source for c in sc.components if c.axis == "confidence" and c.adjusted < 0.5 and c.weight > 0]
    for sec in secs:
        sec.update({"quality": round(sc.quality, 3), "difficulty": round(sc.difficulty, 1), "confidence": round(sc.confidence, 3),
                    "confidence_by_source": sc.confidence_by_source, "low_confidence_sources": low,
                    "history": meta[sid], "components": [{"name": c.name, "axis": c.axis, "detail": c.detail} for c in sc.components]})

TREE = {"kind": "group", "title": "BS Computer Science", "operator": "all", "citation": {"catalog": "2026-27 Georgia Tech Catalog", "page": 214}, "children": [
    {"kind": "group", "title": "CS Core", "operator": "all", "citation": {"catalog": "2026-27 Georgia Tech Catalog", "page": 214}, "children": [
        {"kind": "course", "code": "CS 1301"}, {"kind": "course", "code": "CS 1331"}, {"kind": "course", "code": "CS 1332"}, {"kind": "course", "code": "CS 2050"},
        {"kind": "course", "code": "CS 2110"}, {"kind": "course", "code": "CS 2340"}, {"kind": "course", "code": "CS 3510"}]},
    {"kind": "group", "title": "Mathematics", "operator": "all", "citation": {"catalog": "2026-27 Georgia Tech Catalog", "page": 215}, "children": [
        {"kind": "course", "code": "MATH 1551"}, {"kind": "course", "code": "MATH 1552"}, {"kind": "course", "code": "MATH 1554"},
        {"kind": "group", "title": "Multivariable calculus", "operator": "any_n", "min_count": 1, "children": [{"kind": "course", "code": "MATH 2550"}, {"kind": "course", "code": "MATH 2551"}]},
        {"kind": "course", "code": "MATH 3012"}]},
    {"kind": "group", "title": "Science", "operator": "all", "citation": {"catalog": "2026-27 Georgia Tech Catalog", "page": 215}, "children": [
        {"kind": "course", "code": "PHYS 2211"}, {"kind": "course", "code": "PHYS 2212"}]},
    {"kind": "group", "title": "Core IMPACTS: Humanities", "operator": "any_n", "min_count": 1, "citation": {"catalog": "2026-27 Georgia Tech Catalog", "page": 41}, "children": [
        {"kind": "course_list", "min_count": 2, "courses": ["ENGL 1101", "ENGL 1102", "HIST 2111", "HIST 2112", "LMC 2100"]}]},
    {"kind": "group", "title": "Core IMPACTS: Social Sciences", "operator": "any_n", "min_count": 1, "citation": {"catalog": "2026-27 Georgia Tech Catalog", "page": 42}, "children": [
        {"kind": "course_list", "min_count": 2, "courses": ["PSYC 1101", "ECON 2100", "ECON 2105", "ECON 2106", "HIST 2111"]}]},
]}

OUT.parent.mkdir(parents=True, exist_ok=True)
json.dump({"term": {"code": "202608", "name": "Fall 2026", "institution": "Georgia Institute of Technology"},
           "data_note": "Sections, times, instructors, CRNs and seat counts are the real Fall 2026 feed. Grade history and ratings are SYNTHETIC demo data scored by the scoring service.",
           "leniency_model": {"slope": model.slope, "intercept": model.intercept, "r_squared": model.r_squared, "n": model.n_sections} if model else None,
           "courses": courses, "sections": sections, "requirement_tree": TREE}, open(OUT, "w"))
print(len(courses), "courses", len(sections), "sections ->", OUT)
