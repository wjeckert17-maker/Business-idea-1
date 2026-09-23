"""Evaluation 2: replay real registrations through the optimizer.

Input (JSONL): {"student_id", "completed": [...], "crns": [...], "min_credits"?, "max_credits"?, "blocked"?: [{weekday,start,end}]}
Corpus: sections (with quality/difficulty/history), courses, requirement_tree — the same shapes as web/data/demo.json.
For each student we build the PlanRequest the optimizer would have seen (credits = their actual load ±1 unless given,
their blocked windows if known) and ask for top-5. Reported per student and in aggregate:
  * course-set hit: their set of courses equals one of our top-5 course sets
  * section hit:    their exact CRN set equals one of our top-5
  * avg instructor GPA of our #1 vs theirs (mean of section history means, with how many sections had history)
  * time conflicts in theirs and in our #1
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sched.models import Constraints, Course, Meeting, PlanRequest, Section, WeightVector, Weights
from sched.api import plan


@dataclass
class StudentResult:
    student_id: str
    their_courses: List[str]
    their_crns: List[str]
    their_credits: float
    course_hit_rank: Optional[int]     # 1..5 or None
    section_hit_rank: Optional[int]
    top_course_sets: List[List[str]]
    top1_crns: List[str]
    my_gpa: Optional[float]
    my_gpa_sections: int
    their_gpa: Optional[float]
    their_gpa_sections: int
    my_conflicts: int
    their_conflicts: int
    note: str = ""


@dataclass
class ScheduleReport:
    students: int
    course_hit_rate: float
    section_hit_rate: float
    mean_my_gpa: Optional[float]
    mean_their_gpa: Optional[float]
    mean_my_conflicts: float
    mean_their_conflicts: float
    per_student: List[StudentResult]
    skipped: Dict[str, str]

    def to_dict(self): return asdict(self)


def _sections(corpus: Dict[str, Any]) -> Dict[str, Section]:
    out = {}
    for s in corpus["sections"]:
        out[s["crn"]] = Section(s["crn"], s["course_code"], tuple(Meeting(m["weekday"], m["start"], m["end"]) for m in s["meetings"]),
                                s.get("instructor"), s.get("capacity"), s.get("enrolled"), s.get("quality"), s.get("difficulty"), s.get("credits"))
    return out


def conflicts(secs: List[Section]) -> int:
    n = 0
    for i, a in enumerate(secs):
        for b in secs[i + 1:]:
            if any(p.overlaps(q) for p in a.meetings for q in b.meetings):
                n += 1
    return n


def gpa_of(crns: List[str], hist: Dict[str, Optional[float]]):
    vals = [hist[c] for c in crns if hist.get(c) is not None]
    return (sum(vals) / len(vals) if vals else None), len(vals)


def evaluate(corpus: Dict[str, Any], registrations: List[dict], weights: Optional[Weights] = None, k: int = 5, time_limit: float = 5.0) -> ScheduleReport:
    secs = _sections(corpus)
    courses = {c: Course(c, float(v["credits"]), tuple(tuple(a) for a in v.get("prereqs") or ()), v.get("title")) for c, v in corpus["courses"].items()}
    hist = {s["crn"]: (s.get("history") or {}).get("mean_gpa") for s in corpus["sections"]}
    results: List[StudentResult] = []
    skipped: Dict[str, str] = {}
    for r in registrations:
        sid = r["student_id"]
        theirs = [secs[c] for c in r["crns"] if c in secs]
        if len(theirs) != len(r["crns"]):
            skipped[sid] = "some registered CRNs are not in the corpus"; continue
        their_courses = sorted({s.course_code for s in theirs})
        credits = sum((s.credits if s.credits is not None else courses[s.course_code].credits) for s in theirs)
        blocked = tuple(Meeting(b["weekday"], b["start"], b["end"]) for b in r.get("blocked") or ())
        cons = Constraints(float(r.get("min_credits", max(0.0, credits - 1))), float(r.get("max_credits", credits + 1)), blocked, (), 0.9,
                           allow_courses_outside_requirements=r.get("allow_outside", True))
        req = PlanRequest(frozenset(r.get("completed", [])), courses, list(secs.values()), corpus.get("requirement_tree"), cons, weights or Weights(), k, time_limit)
        res = plan(req)
        sets = [sorted(s.courses) for s in res.schedules]
        crn_sets = [sorted(c.section_id for c in s.sections) for s in res.schedules]
        c_hit = next((i + 1 for i, s in enumerate(sets) if s == their_courses), None)
        s_hit = next((i + 1 for i, s in enumerate(crn_sets) if s == sorted(r["crns"])), None)
        top1 = crn_sets[0] if crn_sets else []
        my_g, my_n = gpa_of(top1, hist); th_g, th_n = gpa_of(r["crns"], hist)
        results.append(StudentResult(sid, their_courses, sorted(r["crns"]), credits, c_hit, s_hit, sets, top1, my_g, my_n, th_g, th_n,
                                     conflicts([secs[c] for c in top1]), conflicts(theirs),
                                     "" if res.feasible else f"optimizer relaxed {res.relaxation.relaxed if res.relaxation else 'nothing'}"))
    n = len(results)
    def mean(xs): xs = [x for x in xs if x is not None]; return (sum(xs) / len(xs)) if xs else None
    return ScheduleReport(n, (sum(1 for x in results if x.course_hit_rank) / n) if n else 0.0, (sum(1 for x in results if x.section_hit_rank) / n) if n else 0.0,
                          mean([x.my_gpa for x in results]), mean([x.their_gpa for x in results]),
                          (sum(x.my_conflicts for x in results) / n) if n else 0.0, (sum(x.their_conflicts for x in results) / n) if n else 0.0, results, skipped)


def render(r: ScheduleReport) -> str:
    f = lambda x: "n/a" if x is None else f"{x:.3f}"
    L = ["# Schedule preference replay", "", f"* students replayed: {r.students}" + (f"; skipped: {len(r.skipped)}" if r.skipped else ""),
         f"* student's own **course set** in our top 5: **{r.course_hit_rate:.1%}**; exact **section set** in top 5: {r.section_hit_rate:.1%}",
         f"* mean instructor GPA — ours (#1): {f(r.mean_my_gpa)}; theirs: {f(r.mean_their_gpa)}",
         f"* mean time conflicts — ours: {r.mean_my_conflicts:.2f}; theirs: {r.mean_their_conflicts:.2f}", "",
         "| student | their courses | course hit | section hit | GPA ours/theirs | conflicts ours/theirs | note |", "|---|---|---|---|---|---|---|"]
    for s in r.per_student:
        L.append(f"| {s.student_id} | {', '.join(s.their_courses)} | {s.course_hit_rank or '—'} | {s.section_hit_rank or '—'} | {f(s.my_gpa)} (n={s.my_gpa_sections}) / {f(s.their_gpa)} (n={s.their_gpa_sections}) | {s.my_conflicts}/{s.their_conflicts} | {s.note} |")
    return "\n".join(L) + "\n"
