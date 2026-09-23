"""CP-SAT model, top-K diverse search, per-course scoring, and minimum-cost relaxation."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ortools.sat.python import cp_model

from .models import (ChosenSection, Constraints, Course, Explanation, Meeting, PlanRequest, Relaxation, Schedule, Section,
                     WeightVector, MINUTES_PER_DAY)
from .prereq import needed_courses, prereqs_satisfied, unlock_count

SCALE = 1000                 # points -> integer objective units
OBJECTIVES = ("quality", "difficulty", "start_time", "unlock", "fullness", "days_on_campus", "idle_gap")
MAXIMIZE = {"quality"}       # everything else is minimized (start_time sign depends on preference)


# ---------------------------------------------------------------------------
class Candidates:
    """Which courses and sections are eligible, and each section's per-course points."""

    def __init__(self, req: PlanRequest):
        self.req = req
        c = req.constraints
        self.needed, self.groups_of = needed_courses(req.requirement_tree, req.completed)
        self.excluded: Dict[str, str] = {}
        offered = {s.course_code for s in req.sections}
        eligible: Set[str] = set()
        for code in sorted(offered):
            course = req.courses.get(code)
            if course is None:
                self.excluded[code] = "not in course catalog"; continue
            if code in req.completed:
                self.excluded[code] = "already completed"; continue
            if not prereqs_satisfied(course, req.completed):
                self.excluded[code] = f"prerequisites not met: {' or '.join(' & '.join(a) for a in course.prereqs)}"; continue
            if req.requirement_tree is not None and code not in self.needed and not c.allow_courses_outside_requirements and code not in c.required_courses:
                self.excluded[code] = "does not satisfy any remaining requirement"; continue
            eligible.add(code)
        for code in c.required_courses:
            if code not in offered:
                self.excluded[code] = "required but not offered this term"
        self.courses = sorted(eligible)
        self.sections: List[Section] = [s for s in req.sections if s.course_code in eligible and s.meetings is not None]
        self.unlock = {code: unlock_count(code, self.needed or offered, req.courses, req.completed) for code in self.courses}
        self.max_unlock = max(self.unlock.values(), default=0) or 1
        self.terms: Dict[str, Dict[str, float]] = {s.section_id: self.section_terms(s) for s in self.sections}
        self.points: Dict[str, float] = {sid: sum(t.values()) for sid, t in self.terms.items()}

    def credits_of(self, s: Section) -> float:
        return s.credits if s.credits is not None else self.req.courses[s.course_code].credits

    def section_terms(self, s: Section) -> Dict[str, float]:
        """Signed points per per-course objective for one section under its course's weight vector."""
        w = self.req.weights.for_course(s.course_code)
        t: Dict[str, float] = {}
        if w.quality and s.quality is not None:
            t["quality"] = w.quality * s.quality                              # standardized, maximize
        if w.difficulty and s.difficulty is not None:
            t["difficulty"] = -w.difficulty * s.difficulty / 100.0            # percentile -> 0..1, minimize
        if w.start_time and s.earliest_start() is not None:
            frac = s.earliest_start() / MINUTES_PER_DAY
            t["start_time"] = -w.start_time * (frac if w.start_preference == "earlier" else (1.0 - frac))
        if w.unlock:
            t["unlock"] = w.unlock * self.unlock.get(s.course_code, 0) / self.max_unlock
        if w.fullness:
            f = s.fullness()
            if f is not None and f > self.req.constraints.full_threshold:
                t["fullness"] = -w.fullness * (1.0 + (f - self.req.constraints.full_threshold) / max(1e-9, 1 - self.req.constraints.full_threshold))
        return t


# ---------------------------------------------------------------------------
class Model:
    """One CP-SAT model over the candidate sections. `relaxable` maps constraint names to indicator
    literals: the constraint is enforced only when its indicator is true."""

    def __init__(self, cand: Candidates, relaxable: bool = False):
        self.cand, self.req = cand, cand.req
        self.m = cp_model.CpModel()
        self.x: Dict[str, cp_model.IntVar] = {s.section_id: self.m.NewBoolVar(f"x[{s.section_id}]") for s in cand.sections}
        self.y: Dict[str, cp_model.IntVar] = {c: self.m.NewBoolVar(f"y[{c}]") for c in cand.courses}
        self.relax: Dict[str, cp_model.IntVar] = {}
        self._build(relaxable)

    def _ind(self, name: str, relaxable: bool):
        if not relaxable:
            return None
        v = self.m.NewBoolVar(f"keep[{name}]")
        self.relax[name] = v
        return v

    def _build(self, relaxable: bool):
        m, req, cand = self.m, self.req, self.cand
        c = req.constraints
        by_course: Dict[str, List[Section]] = defaultdict(list)
        for s in cand.sections:
            by_course[s.course_code].append(s)
        # a course appears at most once: y_c = Σ x_s over its sections, ≤ 1
        for code, secs in by_course.items():
            m.Add(sum(self.x[s.section_id] for s in secs) == self.y[code])
        # a schedule is at least one course; an empty schedule is never an answer
        if self.y:
            m.Add(sum(self.y.values()) >= 1)
        else:
            m.Add(0 == 1)
        # no overlaps
        for i, a in enumerate(cand.sections):
            for b in cand.sections[i + 1:]:
                if a.course_code != b.course_code and any(p.overlaps(q) for p in a.meetings for q in b.meetings):
                    m.AddBoolOr([self.x[a.section_id].Not(), self.x[b.section_id].Not()])
        # blocked windows (each relaxable individually)
        for i, win in enumerate(c.blocked):
            ind = self._ind(f"blocked[{i}]", relaxable)
            for s in cand.sections:
                if any(mt.overlaps(win) for mt in s.meetings):
                    if ind is None:
                        m.Add(self.x[s.section_id] == 0)
                    else:
                        m.Add(self.x[s.section_id] == 0).OnlyEnforceIf(ind)
        # credits (scaled to tenths to stay integral)
        cr = sum(int(round(cand.credits_of(s) * 10)) * self.x[s.section_id] for s in cand.sections)
        lo, hi = int(round(c.min_credits * 10)), int(round(c.max_credits * 10))
        ind = self._ind("min_credits", relaxable)
        (m.Add(cr >= lo) if ind is None else m.Add(cr >= lo).OnlyEnforceIf(ind))
        ind = self._ind("max_credits", relaxable)
        (m.Add(cr <= hi) if ind is None else m.Add(cr <= hi).OnlyEnforceIf(ind))
        # required courses
        for code in c.required_courses:
            ind = self._ind(f"required[{code}]", relaxable)
            if code in self.y:
                (m.Add(self.y[code] == 1) if ind is None else m.Add(self.y[code] == 1).OnlyEnforceIf(ind))
            elif ind is None:
                m.Add(0 == 1)                    # required but not eligible: infeasible
            else:
                m.Add(0 == 1).OnlyEnforceIf(ind)
        # schedule-level objectives: days on campus, longest idle gap
        w0 = req.weights.default
        self.day_used: Dict[int, cp_model.IntVar] = {}
        self.gap_max = m.NewIntVar(0, MINUTES_PER_DAY, "gap_max")
        secs_by_day: Dict[int, List[Tuple[Section, Meeting]]] = defaultdict(list)
        for s in cand.sections:
            for mt in s.meetings:
                secs_by_day[mt.weekday].append((s, mt))
        for d, items in secs_by_day.items():
            used = m.NewBoolVar(f"day[{d}]")
            self.day_used[d] = used
            xs = list({s.section_id for s, _ in items})
            for sid in xs:
                m.AddImplication(self.x[sid], used)
            m.Add(sum(self.x[sid] for sid in xs) >= used)
            if w0.idle_gap:
                first = m.NewIntVar(0, MINUTES_PER_DAY, f"first[{d}]")
                last = m.NewIntVar(0, MINUTES_PER_DAY, f"last[{d}]")
                for s, mt in items:
                    m.Add(first <= mt.start).OnlyEnforceIf(self.x[s.section_id])
                    m.Add(last >= mt.end).OnlyEnforceIf(self.x[s.section_id])
                class_min = sum((mt.end - mt.start) * self.x[s.section_id] for s, mt in items)
                gap = m.NewIntVar(0, MINUTES_PER_DAY, f"gap[{d}]")
                m.Add(gap >= last - first - class_min).OnlyEnforceIf(used)
                m.Add(self.gap_max >= gap)
        # objective
        obj = []
        for s in cand.sections:
            obj.append(int(round(cand.points[s.section_id] * SCALE)) * self.x[s.section_id])
        if w0.days_on_campus:
            obj.append(-int(round(w0.days_on_campus * SCALE)) * sum(self.day_used.values()))
        if w0.idle_gap:
            obj.append(-int(round(w0.idle_gap * SCALE / 60.0)) * self.gap_max)   # points per idle hour
        self.objective = sum(obj) if obj else 0
        m.Maximize(self.objective)

    def exclude_course_set(self, codes: Sequence[str]) -> None:
        """Forbid any schedule whose chosen course set equals `codes` exactly."""
        inside = [self.y[c] for c in codes if c in self.y]
        outside = [self.y[c] for c in self.y if c not in codes]
        self.m.Add(sum(inside) - sum(outside) <= len(inside) - 1)

    def solve(self, time_limit: float) -> Tuple[str, Optional[List[Section]]]:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = 8
        status = solver.Solve(self.m)
        name = solver.StatusName(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return name, None
        chosen = [s for s in self.cand.sections if solver.Value(self.x[s.section_id])]
        self.last_solver = solver
        return name, chosen


# ---------------------------------------------------------------------------
def evaluate(cand: Candidates, chosen: Sequence[Section]) -> Tuple[float, Dict[str, float], Dict[str, float]]:
    """Ground-truth metrics and points for a concrete schedule (Python, independent of the model)."""
    req, w0 = cand.req, cand.req.weights.default
    pts: Dict[str, float] = defaultdict(float)
    for s in chosen:
        for k, v in cand.terms[s.section_id].items():
            pts[k] += v
    by_day: Dict[int, List[Meeting]] = defaultdict(list)
    for s in chosen:
        for mt in s.meetings:
            by_day[mt.weekday].append(mt)
    days = len(by_day)
    longest_gap = 0
    for mts in by_day.values():
        mts = sorted(mts, key=lambda m: m.start)
        for a, b in zip(mts, mts[1:]):
            longest_gap = max(longest_gap, b.start - a.end)
    if w0.days_on_campus:
        pts["days_on_campus"] -= w0.days_on_campus * days
    if w0.idle_gap:
        pts["idle_gap"] -= w0.idle_gap * longest_gap / 60.0
    starts = [s.earliest_start() for s in chosen if s.earliest_start() is not None]
    metrics = {
        "credits": sum(cand.credits_of(s) for s in chosen),
        "quality_sum": sum(s.quality or 0.0 for s in chosen),
        "difficulty_mean": (sum(s.difficulty for s in chosen if s.difficulty is not None) / max(1, sum(1 for s in chosen if s.difficulty is not None))),
        "days_on_campus": float(days),
        "longest_gap_min": float(longest_gap),
        "mean_start_min": (sum(starts) / len(starts)) if starts else 0.0,
        "unlock_count": float(sum(cand.unlock.get(s.course_code, 0) for s in chosen)),
        "overfull_sections": float(sum(1 for s in chosen if (s.fullness() or 0) > req.constraints.full_threshold)),
    }
    return sum(pts.values()), dict(pts), metrics


def runner_up(cand: Candidates, chosen: Sequence[Section], course: str) -> Optional[Dict[str, Any]]:
    """Best alternative section for `course` with every other chosen section held fixed."""
    current = next(s for s in chosen if s.course_code == course)
    others = [s for s in chosen if s.course_code != course]
    best = None
    for alt in cand.sections:
        if alt.course_code != course or alt.section_id == current.section_id:
            continue
        clashes = [o.section_id for o in others if any(p.overlaps(q) for p in alt.meetings for q in o.meetings)]
        blocked = [f"{b.weekday}:{b.start}-{b.end}" for b in cand.req.constraints.blocked if any(p.overlaps(b) for p in alt.meetings)]
        clash = bool(clashes or blocked)
        total_alt, _, _ = evaluate(cand, others + [alt])
        total_cur, _, _ = evaluate(cand, list(chosen))
        why = "lower total score" if not clash else ("overlaps " + ", ".join(
            [f"section {c}" for c in clashes] + [f"blocked window {b}" for b in blocked]))
        cand_row = {"section_id": alt.section_id, "score": round(cand.points[alt.section_id], 4),
                    "delta_total_points": round(total_alt - total_cur, 4), "feasible_swap": not clash,
                    "clashes_with": clashes, "blocked_by": blocked, "why_not": why}
        if best is None or (cand_row["feasible_swap"], cand_row["delta_total_points"]) > (best["feasible_swap"], best["delta_total_points"]):
            best = cand_row
    return best


def rank_explanations(cand: Candidates, solutions: List[Tuple[List[Section], float, Dict[str, float], Dict[str, float]]]) -> List[Explanation]:
    """Per-objective ranks across the returned set; won = rank 1, lost = last (only when the set has >1)."""
    out = []
    n = len(solutions)
    for i, (_, _, pts, metrics) in enumerate(solutions):
        ranks: Dict[str, int] = {}
        won, lost = [], []
        for obj in OBJECTIVES:
            vals = [p.get(obj, 0.0) for _, _, p, _ in solutions]
            if all(v == 0.0 for v in vals):
                continue
            better = sum(1 for v in vals if v > pts.get(obj, 0.0))          # points are signed: higher is better for all
            ranks[obj] = better + 1
            if n > 1 and ranks[obj] == 1 and vals.count(pts.get(obj, 0.0)) < n:
                won.append(obj)
            if n > 1 and pts.get(obj, 0.0) == min(vals) and vals.count(pts.get(obj, 0.0)) < n:
                lost.append(obj)
        out.append(Explanation(metrics=metrics, objective_points={k: round(v, 4) for k, v in pts.items()}, won=won, lost=lost, rank_by_objective=ranks))
    return out


# ---------------------------------------------------------------------------
def top_k(cand: Candidates, k: int, time_limit: float) -> Tuple[str, List[List[Section]]]:
    model = Model(cand)
    found: List[List[Section]] = []
    status = "UNKNOWN"
    for _ in range(k):
        status, chosen = model.solve(time_limit)
        if chosen is None:
            break
        found.append(chosen)
        model.exclude_course_set(sorted({s.course_code for s in chosen}))
    return status, found


def minimum_relaxation(cand: Candidates, time_limit: float) -> Optional[Relaxation]:
    """Minimum-cost set of relaxable constraints to drop so that a schedule exists."""
    model = Model(cand, relaxable=True)
    costs = cand.req.relaxation_costs
    def cost_of(name: str) -> float:
        return costs.get(name.split("[")[0], 1.0)
    penalty = sum(int(round(cost_of(n) * SCALE)) * (1 - v) for n, v in model.relax.items())
    model.m.Minimize(penalty)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    status = solver.Solve(model.m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    dropped = [n for n, v in model.relax.items() if not solver.Value(v)]
    if not dropped:
        return None
    return Relaxation(dropped, sum(cost_of(n) for n in dropped),
                      "no schedule satisfies every hard constraint; this is the cheapest set to drop (costs: " + ", ".join(f"{n}={cost_of(n):g}" for n in dropped) + ")")


def apply_relaxation(req: PlanRequest, rel: Relaxation) -> PlanRequest:
    c = req.constraints
    blocked = tuple(w for i, w in enumerate(c.blocked) if f"blocked[{i}]" not in rel.relaxed)
    required = tuple(r for r in c.required_courses if f"required[{r}]" not in rel.relaxed)
    from dataclasses import replace
    c2 = replace(c, blocked=blocked, required_courses=required,
                 min_credits=0.0 if "min_credits" in rel.relaxed else c.min_credits,
                 max_credits=1e9 if "max_credits" in rel.relaxed else c.max_credits)
    return replace(req, constraints=c2)
