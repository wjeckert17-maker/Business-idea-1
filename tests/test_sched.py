"""Hand-built fixtures where the best schedule is known."""
from dataclasses import replace

import pytest

from sched import (Constraints, Course, Meeting, PlanRequest, Section, TimeWindow, WeightVector, Weights, plan, weights_for_groups)

M, T, W, R, F = 1, 2, 3, 4, 5


def mt(days, start, end):
    return tuple(Meeting(d, start, end) for d in days)


COURSES = {
    "CS 101": Course("CS 101", 3), "CS 201": Course("CS 201", 3, prereqs=(("CS 101",),)), "CS 301": Course("CS 301", 3, prereqs=(("CS 201",),)),
    "MATH 101": Course("MATH 101", 4), "HIST 101": Course("HIST 101", 3), "ENG 101": Course("ENG 101", 3), "ART 101": Course("ART 101", 3),
}
TREE = {"kind": "group", "title": "Degree", "operator": "all", "children": [
    {"kind": "group", "title": "Major", "operator": "all", "children": [
        {"kind": "course", "code": "CS 101"}, {"kind": "course", "code": "CS 201"}, {"kind": "course", "code": "CS 301"}, {"kind": "course", "code": "MATH 101"}]},
    {"kind": "group", "title": "Gen Ed", "operator": "any_n", "min_count": 1, "children": [
        {"kind": "course_list", "min_count": 2, "courses": ["HIST 101", "ENG 101", "ART 101"]}]}]}


def sec(sid, code, days, start, end, quality=0.0, difficulty=50.0, cap=30, enrolled=10):
    return Section(sid, code, mt(days, start, end), capacity=cap, enrolled=enrolled, quality=quality, difficulty=difficulty)


def base_sections():
    return [
        sec("cs101-a", "CS 101", (M, W), 9 * 60, 10 * 60, quality=1.0, difficulty=40),
        sec("cs101-b", "CS 101", (T, R), 14 * 60, 15 * 60 + 30, quality=0.2, difficulty=30),
        sec("math-a", "MATH 101", (M, W, F), 10 * 60, 11 * 60, quality=0.5, difficulty=70),
        sec("math-b", "MATH 101", (T, R), 9 * 60, 10 * 60 + 30, quality=-0.5, difficulty=60),
        sec("hist-a", "HIST 101", (M, W), 9 * 60, 10 * 60, quality=1.5, difficulty=80),     # overlaps cs101-a
        sec("hist-b", "HIST 101", (T, R), 11 * 60, 12 * 60 + 30, quality=0.0, difficulty=20),
        sec("eng-a", "ENG 101", (F, ), 13 * 60, 16 * 60, quality=0.8, difficulty=50),
        sec("art-a", "ART 101", (M, W), 15 * 60, 16 * 60, quality=0.3, difficulty=10),
    ]


def req(**kw):
    d = dict(completed=frozenset(), courses=COURSES, sections=base_sections(), requirement_tree=TREE,
             constraints=Constraints(min_credits=9, max_credits=13), weights=Weights(), k=5, time_limit_s=5)
    d.update(kw)
    return PlanRequest(**d)


def courses_of(s):
    return set(s.courses)


def section_ids(s):
    return {c.course_code: c.section_id for c in s.sections}


# --------------------------------------------------------------------------- hard constraints
def test_hard_constraints_hold_on_every_returned_schedule():
    res = plan(req())
    assert res.feasible and res.schedules
    for s in res.schedules:
        assert 9 <= s.credits <= 13
        ids = section_ids(s)
        assert len(ids) == len(s.sections)                       # a course at most once
        chosen = [x for x in base_sections() if x.section_id in ids.values()]
        for i, a in enumerate(chosen):
            for b in chosen[i + 1:]:
                assert not any(p.overlaps(q) for p in a.meetings for q in b.meetings)
    assert "CS 201" in res.excluded_courses or "CS 201" not in {c for s in res.schedules for c in s.courses}


def test_prerequisites_gate_candidates():
    secs = base_sections() + [sec("cs201-a", "CS 201", (T, R), 16 * 60, 17 * 60, quality=2.0, difficulty=10)]
    res = plan(req(sections=secs))
    assert res.excluded_courses["CS 201"].startswith("prerequisites not met")
    res2 = plan(req(sections=secs, completed=frozenset({"CS 101"})))
    assert "CS 201" in res2.candidate_courses and res2.excluded_courses["CS 101"] == "already completed"
    assert "CS 201" in res2.schedules[0].courses                  # quality 2.0, easy: it wins


def test_blocked_window_is_respected():
    work = (TimeWindow(M, 8 * 60, 12 * 60), TimeWindow(W, 8 * 60, 12 * 60))    # MW mornings
    res = plan(req(constraints=Constraints(min_credits=9, max_credits=13, blocked=work)))
    for s in res.schedules:
        for c in s.sections:
            assert c.section_id not in ("cs101-a", "math-a", "hist-a")


def test_courses_outside_requirements_are_excluded_unless_allowed():
    secs = base_sections() + [sec("phil-a", "PHIL 101", (F,), 9 * 60, 10 * 60, quality=3.0, difficulty=0)]
    courses = dict(COURSES, **{"PHIL 101": Course("PHIL 101", 3)})
    res = plan(req(sections=secs, courses=courses))
    assert res.excluded_courses["PHIL 101"] == "does not satisfy any remaining requirement"
    res2 = plan(req(sections=secs, courses=courses, constraints=Constraints(9, 13, allow_courses_outside_requirements=True)))
    assert "PHIL 101" in res2.candidate_courses


# --------------------------------------------------------------------------- per-course weights
def test_per_course_weights_pick_quality_for_major_and_ease_for_gen_ed():
    """CS 101: section a is higher quality but harder; HIST 101: section a is high quality but hard.
    Major weights -> quality wins -> cs101-a. Gen-ed weights -> ease wins -> hist-b. Then hist-b (TR 11)
    does not clash with cs101-a (MW 9)."""
    w = Weights(default=WeightVector(quality=1.0, difficulty=1.0),
                per_course={"CS 101": WeightVector(quality=5.0, difficulty=0.0), "HIST 101": WeightVector(quality=0.0, difficulty=5.0)})
    pinned = Constraints(min_credits=6, max_credits=13, required_courses=("CS 101", "HIST 101"))
    res = plan(req(weights=w, constraints=pinned))
    best = res.schedules[0]
    ids = section_ids(best)
    assert ids["CS 101"] == "cs101-a" and ids.get("HIST 101") == "hist-b"
    cs = next(c for c in best.sections if c.course_code == "CS 101")
    assert cs.weights.quality == 5.0 and cs.terms["quality"] == pytest.approx(5.0) and "difficulty" not in cs.terms
    # flip the vectors and the choices flip
    w2 = Weights(per_course={"CS 101": WeightVector(quality=0.0, difficulty=5.0), "HIST 101": WeightVector(quality=5.0, difficulty=0.0)})
    ids2 = section_ids(plan(req(weights=w2, constraints=pinned)).schedules[0])
    assert ids2["CS 101"] == "cs101-b" and ids2.get("HIST 101") == "hist-a"


def test_weights_for_groups_expands_titles_to_courses():
    w = weights_for_groups(TREE, {"Major": WeightVector(quality=3.0), "Gen Ed": WeightVector(difficulty=3.0)})
    assert w.for_course("CS 301").quality == 3.0 and w.for_course("HIST 101").difficulty == 3.0
    assert w.for_course("XYZ 1") == w.default


# --------------------------------------------------------------------------- soft objectives
def test_days_on_campus_weight_clusters_the_week():
    w = Weights(default=WeightVector(quality=0.0, difficulty=0.0, days_on_campus=10.0))
    res = plan(req(weights=w, constraints=Constraints(min_credits=6, max_credits=7)))
    best = res.schedules[0]
    assert best.explanation.metrics["days_on_campus"] == 2.0            # e.g. CS 101 + HIST/ART on MW, or TR pair


def test_idle_gap_weight_prefers_back_to_back():
    secs = [sec("a", "CS 101", (M,), 9 * 60, 10 * 60), sec("b1", "MATH 101", (M,), 10 * 60, 11 * 60), sec("b2", "MATH 101", (M,), 15 * 60, 16 * 60, quality=0.5)]
    w = Weights(default=WeightVector(quality=1.0, difficulty=0.0, idle_gap=2.0))
    res = plan(req(sections=secs, weights=w, constraints=Constraints(min_credits=7, max_credits=7)))
    assert section_ids(res.schedules[0])["MATH 101"] == "b1"            # 4h gap costs 8 points > 0.5 quality
    assert res.schedules[0].explanation.metrics["longest_gap_min"] == 0.0


def test_start_preference_earlier_and_later():
    secs = [sec("early", "CS 101", (M,), 8 * 60, 9 * 60), sec("late", "CS 101", (M,), 17 * 60, 18 * 60)]
    c = Constraints(min_credits=3, max_credits=3)
    early = plan(req(sections=secs, weights=Weights(default=WeightVector(quality=0, difficulty=0, start_time=1.0, start_preference="earlier")), constraints=c))
    late = plan(req(sections=secs, weights=Weights(default=WeightVector(quality=0, difficulty=0, start_time=1.0, start_preference="later")), constraints=c))
    assert section_ids(early.schedules[0])["CS 101"] == "early" and section_ids(late.schedules[0])["CS 101"] == "late"


def test_fullness_penalty_avoids_sections_above_90_percent():
    secs = [sec("full", "CS 101", (M,), 9 * 60, 10 * 60, quality=0.3, cap=30, enrolled=29), sec("open", "CS 101", (T,), 9 * 60, 10 * 60, quality=0.0, cap=30, enrolled=10)]
    c = Constraints(min_credits=3, max_credits=3)
    res = plan(req(sections=secs, weights=Weights(default=WeightVector(quality=1.0, difficulty=0, fullness=1.0)), constraints=c))
    assert section_ids(res.schedules[0])["CS 101"] == "open"
    res2 = plan(req(sections=secs, weights=Weights(default=WeightVector(quality=1.0, difficulty=0, fullness=0.0)), constraints=c))
    assert section_ids(res2.schedules[0])["CS 101"] == "full"


def test_unlock_weight_prefers_prerequisite_courses():
    secs = [sec("cs101", "CS 101", (M,), 9 * 60, 10 * 60, quality=0.0), sec("hist", "HIST 101", (T,), 9 * 60, 10 * 60, quality=0.5)]
    c = Constraints(min_credits=3, max_credits=3)
    res = plan(req(sections=secs, weights=Weights(default=WeightVector(quality=1.0, difficulty=0, unlock=2.0)), constraints=c))
    assert res.schedules[0].courses == ("CS 101",)                     # unlocks CS 201; HIST unlocks nothing
    assert res.schedules[0].explanation.metrics["unlock_count"] == 1.0


# --------------------------------------------------------------------------- top-K, explanations
def test_top_k_schedules_have_distinct_course_sets_and_are_ranked():
    res = plan(req())
    sets = [courses_of(s) for s in res.schedules]
    assert len(sets) == len({frozenset(x) for x in sets}) and len(sets) >= 3
    assert [s.total_points for s in res.schedules] == sorted((s.total_points for s in res.schedules), reverse=True)
    assert [s.rank for s in res.schedules] == list(range(1, len(res.schedules) + 1))


def test_explanations_name_runner_ups_and_won_lost():
    res = plan(req(constraints=Constraints(min_credits=6, max_credits=13, required_courses=("CS 101",))))
    best = res.schedules[0]
    cs = next(c for c in best.sections if c.course_code == "CS 101")
    assert cs.runner_up and cs.runner_up["section_id"] != cs.section_id and cs.runner_up["delta_total_points"] <= 0
    assert "feasible_swap" in cs.runner_up and "why_not" in cs.runner_up
    objs = set()
    for s in res.schedules:
        assert set(s.explanation.won).isdisjoint(s.explanation.lost)
        objs |= set(s.explanation.rank_by_objective)
    assert {"quality", "difficulty"} <= objs
    assert sum(1 for s in res.schedules if "quality" in s.explanation.won) >= 1


# --------------------------------------------------------------------------- infeasibility
def test_infeasible_reports_minimum_cost_relaxation_and_still_returns_schedules():
    """Required course ART 101 only meets Monday 15-16, inside a Monday work shift. Dropping that one
    shift (cost 2) is cheaper than dropping the requirement (cost 3); the credit bounds alone cannot help,
    and the Wednesday shift is irrelevant and must stay."""
    secs = [x for x in base_sections() if x.course_code != "ART 101"] + [sec("art-mon", "ART 101", (M,), 15 * 60, 16 * 60, quality=0.3, difficulty=10)]
    shift = (TimeWindow(M, 14 * 60, 18 * 60), TimeWindow(W, 14 * 60, 18 * 60))
    c = Constraints(min_credits=6, max_credits=7, blocked=shift, required_courses=("ART 101",))
    res = plan(req(sections=secs, constraints=c))
    assert not res.feasible and res.relaxation is not None
    assert res.relaxation.relaxed == ["blocked[0]"] and res.relaxation.cost == 2.0
    assert res.schedules and all("ART 101" in s.courses for s in res.schedules)
    assert res.schedules[0].relaxed == res.relaxation.relaxed


def test_infeasible_prefers_dropping_cheapest_constraint():
    c = Constraints(min_credits=20, max_credits=21)     # only ~13 credits exist
    res = plan(req(constraints=c, relaxation_costs={"min_credits": 1.0, "max_credits": 1.0, "blocked": 2.0, "required": 3.0}))
    assert res.relaxation.relaxed == ["min_credits"] and res.schedules
    assert all(s.credits <= 21 for s in res.schedules)


def test_truly_empty_offering_returns_nothing_gracefully():
    res = plan(req(sections=[]))
    assert res.schedules == [] and not res.feasible and res.relaxation is None
