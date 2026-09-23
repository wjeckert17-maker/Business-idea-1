from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional

from .engine import Candidates, apply_relaxation, evaluate, minimum_relaxation, rank_explanations, runner_up, top_k
from .models import ChosenSection, PlanRequest, PlanResult, Relaxation, Schedule, WeightVector, Weights
from .prereq import needed_courses


def plan(req: PlanRequest) -> PlanResult:
    cand = Candidates(req)
    status, found = top_k(cand, req.k, req.time_limit_s)
    relaxation: Optional[Relaxation] = None
    relaxed_names: List[str] = []
    if not found:
        relaxation = minimum_relaxation(cand, req.time_limit_s)
        if relaxation is not None:
            req2 = apply_relaxation(req, relaxation)
            cand = Candidates(req2)
            status, found = top_k(cand, req2.k, req2.time_limit_s)
            relaxed_names = relaxation.relaxed
    solutions = []
    for chosen in found:
        total, pts, metrics = evaluate(cand, chosen)
        solutions.append((chosen, total, pts, metrics))
    solutions.sort(key=lambda t: -t[1])
    explanations = rank_explanations(cand, solutions)
    schedules: List[Schedule] = []
    for rank, ((chosen, total, pts, metrics), ex) in enumerate(zip(solutions, explanations), 1):
        secs = []
        for s in sorted(chosen, key=lambda s: s.course_code):
            secs.append(ChosenSection(s.course_code, s.section_id, round(cand.points[s.section_id], 4), req.weights.for_course(s.course_code),
                                      {k: round(v, 4) for k, v in cand.terms[s.section_id].items()}, runner_up(cand, chosen, s.course_code)))
        schedules.append(Schedule(rank, round(total, 4), metrics["credits"], tuple(sorted({s.course_code for s in chosen})), secs, ex, list(relaxed_names)))
    return PlanResult(schedules, feasible=relaxation is None and bool(found), relaxation=relaxation,
                      candidate_courses=cand.courses, excluded_courses=cand.excluded, solver_status=status)


def weights_for_groups(tree: Dict[str, Any], by_group: Dict[str, WeightVector], default: WeightVector = WeightVector(),
                       completed=frozenset()) -> Weights:
    """'maximize quality for my major, minimize difficulty for gen-eds': map requirement-group titles to
    weight vectors; every course under a matching group (any ancestor) gets that vector."""
    _, groups_of = needed_courses(tree, completed)
    per_course = {}
    for code, path in groups_of.items():
        for title in reversed(path):            # nearest group wins
            if title in by_group:
                per_course[code] = by_group[title]; break
    return Weights(default=default, per_course=per_course)
