"""Harness tests on fixtures with known answers."""
import copy
import json
from pathlib import Path

import pytest

from evalx import fixtures, replay, requirements_eval, schedule_eval, scoring_sanity
from scoring import ScoringConfig, SourceWeights

DEMO = Path(__file__).resolve().parents[1] / "web" / "data" / "demo.json"
TREE = {"kind": "group", "title": "Degree", "operator": "all", "children": [
    {"kind": "group", "title": "Core", "operator": "all", "description": "Complete all of: CS 1301, CS 1331", "children": [
        {"kind": "course", "code": "CS 1301", "source_lines": ["CS 1301 Intro to Computing 3"]}, {"kind": "course", "code": "CS 1331"}]},
    {"kind": "group", "title": "Electives", "operator": "any_n", "min_count": 1, "children": [
        {"kind": "course_list", "min_count": 2, "courses": ["HIST 2111", "HIST 2112", "PSYC 1101"]}]},
    {"kind": "group", "title": "Capstone", "operator": "all", "children": [{"kind": "needs_review", "original_text": "approved by advisor", "reason": "delegated"}]}]}


# ---------------------------------------------------------------- 1. requirements
def test_requirement_evaluator_statuses():
    st = {s.title: s for s in requirements_eval.evaluate(TREE, ["CS 1301", "HIST 2111"])}
    assert st["Core"].satisfied is False and st["Core"].remaining == ["CS 1331"]
    assert st["Electives"].satisfied is False and st["Electives"].remaining == ["HIST 2112", "PSYC 1101"]
    assert st["Capstone"].satisfied is None            # undecidable, never silently satisfied
    assert st["Degree"].satisfied is False
    st2 = {s.title: s for s in requirements_eval.evaluate(TREE, ["CS 1301", "CS 1331", "HIST 2111", "PSYC 1101"])}
    assert st2["Core"].satisfied and st2["Electives"].satisfied and st2["Electives"].remaining == []


def test_agreement_is_100_on_consistent_audits_and_gate_blocks_on_one_disagreement():
    trees = {"2026-27": TREE}
    t = [{"student_id": "A", "catalog_year": "2026-27", "completed": ["CS 1301", "HIST 2111"]}]
    a = [{"student_id": "A", "groups": [{"title": "Core", "satisfied": False, "remaining": ["CS 1331"]}, {"title": "Electives", "satisfied": False, "remaining": ["HIST 2112", "PSYC 1101"]}]}]
    r = requirements_eval.compare(t, a, trees)
    assert r.agreement == 1.0 and r.release_ok and r.disagreements == []
    a2 = copy.deepcopy(a); a2[0]["groups"][0] = {"title": "Core", "satisfied": True, "remaining": []}   # the university disagrees
    r2 = requirements_eval.compare(t, a2, trees)
    assert r2.agreement == 0.5 and not r2.release_ok and len(r2.disagreements) == 1
    d = r2.disagreements[0]
    assert d.group == "Core" and d.audit_satisfied is True and d.engine_satisfied is False and d.engine_remaining == ["CS 1331"]
    assert "Complete all of" in d.catalog_text and d.engine_parse["operator"] == "all"
    md = requirements_eval.render(r2)
    assert "BLOCKED" in md and "Catalog text" in md and "Our parse" in md


def test_requirement_fixture_round_trip():
    corpus = fixtures.build_corpus(str(DEMO), n_sections=30, n_students=3)
    t, a = fixtures.build_requirement_fixture(corpus["requirement_tree"], n=4, corrupt_one=True)
    r = requirements_eval.compare(t, a, {"2026-27": corpus["requirement_tree"]})
    assert r.students == 4 and len(r.disagreements) == 1 and not r.release_ok
    assert r.per_student["T00"]["agreement"] < 1.0 and all(v["agreement"] == 1.0 for s, v in r.per_student.items() if s != "T00")


# ---------------------------------------------------------------- 2. schedules
@pytest.fixture(scope="module")
def corpus():
    return fixtures.build_corpus(str(DEMO), n_sections=40, n_students=6, seed=5)


def test_schedule_replay_reports_hits_gpa_and_conflicts(corpus):
    scored = replay.score_sections(corpus, ScoringConfig())
    regs = corpus["registrations"][:4]
    r = schedule_eval.evaluate(scored, regs, time_limit=3)
    assert r.students == 4 and all(s.their_conflicts == 0 for s in r.per_student)
    assert all(s.my_conflicts == 0 for s in r.per_student)
    # a student whose registration IS our #1 must be a rank-1 hit on both course set and section set
    first = r.per_student[0]
    planted = {"student_id": "PLANTED", "completed": [], "crns": first.top1_crns, "allow_outside": True}
    r2 = schedule_eval.evaluate(scored, [planted], time_limit=3)
    assert r2.per_student[0].course_hit_rank == 1 and r2.per_student[0].section_hit_rank == 1 and r2.course_hit_rate == 1.0
    assert r2.per_student[0].my_gpa == r2.per_student[0].their_gpa
    md = schedule_eval.render(r)
    assert "course set" in md and "PLANTED" not in md


# ---------------------------------------------------------------- 3. scoring sanity
def test_leniency_adjustment_removes_gpa_correlation():
    corpus = fixtures.build_corpus(str(DEMO), n_sections=120, n_students=1, seed=2, leniency_slope=0.9)
    r = scoring_sanity.run(replay._inputs(corpus), ScoringConfig(prior_strength_m=1.0))
    assert r.r_raw_rating_vs_gpa > 0.5, r.r_raw_rating_vs_gpa             # raw ratings track grades
    assert abs(r.r_adjusted_quality_vs_gpa) < 0.1, r.r_adjusted_quality_vs_gpa   # adjusted quality does not
    assert r.adjustment_ok and "adjustment works" in r.verdict


def test_sanity_flags_a_broken_adjustment():
    """Sentiment weight 0 leaves no leniency path; quality comes from syllabus flags only. Simulate a broken
    adjuster by feeding the check a config whose quality is raw rating: r_adj should then be high and the verdict BLOCK."""
    corpus = fixtures.build_corpus(str(DEMO), n_sections=120, n_students=1, seed=2, leniency_slope=0.9)
    inputs = replay._inputs(corpus)
    # monkeypatch: pretend the adjusted quality is the raw rating
    real = scoring_sanity.score_corpus
    def fake(inp, cfg):
        scores, model = real(inp, cfg)
        out = {}
        for sid, sc in scores.items():
            raw = next((c.raw for c in sc.components if c.name == "sentiment_rating_residual"), None)
            out[sid] = sc.__class__(**{**sc.__dict__, "quality": raw if raw is not None else sc.quality})
        return out, model
    scoring_sanity.score_corpus = fake
    try:
        r = scoring_sanity.run(inputs, ScoringConfig(prior_strength_m=1.0))
    finally:
        scoring_sanity.score_corpus = real
    assert not r.adjustment_ok and "NOT WORKING" in r.verdict


def test_demographic_skew_audit_detects_injected_skew_and_reports_null_result():
    fair = fixtures.build_corpus(str(DEMO), n_sections=140, n_students=1, seed=9)
    r = scoring_sanity.run(replay._inputs(fair), ScoringConfig(prior_strength_m=1.0), fair["demographics"], permutations=500)
    assert {s.group for s in r.skew} == {"A", "B"} and all(s.p_value_quality > 0.05 for s in r.skew), [(s.group, s.p_value_quality) for s in r.skew]
    skewed = fixtures.build_corpus(str(DEMO), n_sections=140, n_students=1, seed=9, skew_group="A", skew=0.6)
    r2 = scoring_sanity.run(replay._inputs(skewed), ScoringConfig(prior_strength_m=1.0), skewed["demographics"], permutations=500)
    a = next(s for s in r2.skew if s.group == "A")
    assert a.quality_delta_vs_rest > 0.3 and a.p_value_quality < 0.05
    assert "p<0.05" in scoring_sanity.render(r2) and "group" in scoring_sanity.render(r2)


# ---------------------------------------------------------------- 4. replay + diff
def test_replay_is_versioned_and_diff_distinguishes_same_from_different(tmp_path, corpus):
    small = dict(corpus, registrations=corpus["registrations"][:3])
    a = replay.replay(small, "v1", ScoringConfig(), k=3, time_limit=3)
    b = replay.replay(small, "v1-again", ScoringConfig(), k=3, time_limit=3)
    c = replay.replay(small, "v2-no-sentiment", ScoringConfig(weights=SourceWeights(sentiment=0.0)), k=3, time_limit=3)
    pa, pb = replay.save(a, str(tmp_path)), replay.save(b, str(tmp_path))
    assert Path(pa).exists() and json.load(open(pa))["meta"]["version"] == "v1" and a["meta"]["corpus_hash"] == b["meta"]["corpus_hash"]
    same = replay.diff(a, b)
    assert same["verdict"].startswith("no change") and same["top1_changed"] == [] and same["section_quality_rank_correlation"] > 0.999
    d = replay.diff(a, c)
    assert d["section_quality_rank_correlation"] < 0.999 and ("different" in d["verdict"] or "better" in d["verdict"])
    other = replay.replay(dict(small, registrations=small["registrations"][:2], sections=small["sections"][:-1]), "v3", ScoringConfig(), k=3, time_limit=3)
    assert replay.diff(a, other)["warnings"], "different corpora must be flagged"
    assert "Replay diff" in replay.render_diff(d)
