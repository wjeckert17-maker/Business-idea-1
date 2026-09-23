"""Synthetic corpora where the right answer is known."""
import math
import random
from dataclasses import replace
from datetime import date

import pytest

from scoring import (Component, GradeObservation, ScoringConfig, SectionInput, SentimentObservation, SourceWeights,
                     SyllabusFeatures, score_corpus)
from scoring.stats import confidence_from_n, decay_weight, ols, percentile_rank, shrink

AS_OF = date(2026, 6, 1)


def grades(gpa, n=100, dfw=0.1, when=AS_OF, w=0):
    """Letter counts with the requested mean GPA (A/B/C/D/F mix) and DFW share."""
    df = int(round(n * dfw)); rest = n - df
    a = rest * (gpa - 2.0) / 2.0 if gpa >= 2.0 else 0
    a = int(round(min(rest, max(0, a)))); c = rest - a
    return GradeObservation(when, {"A": a, "C": c, "F": df}, w_count=w, mean_gpa=gpa)   # pin the GPA exactly


def sentiment(rating, n=30, difficulty=3.0, wta=0.7, when=AS_OF):
    return SentimentObservation(when, n, rating=rating, difficulty=difficulty, would_take_again=wta)


def section(sid, dept="CS", level=100, gpa=3.0, rating=3.5, n_g=100, n_s=30, dfw=0.1, syllabus=None, when=AS_OF):
    return SectionInput(sid, dept, level, [grades(gpa, n_g, dfw, when)], [sentiment(rating, n_s, when=when)], syllabus)


# --------------------------------------------------------------------------- stats
def test_shrinkage_formula_and_limits():
    assert shrink(4.5, 5, 3.5, 15) == pytest.approx((5 * 4.5 + 15 * 3.5) / 20)
    assert shrink(4.5, 5, 3.5, 0) == 4.5                 # m = 0 -> raw
    assert shrink(4.5, 1e9, 3.5, 15) == pytest.approx(4.5, abs=1e-6)
    assert shrink(None, 0, 3.5, 15) == 3.5               # no data -> exactly the prior
    assert confidence_from_n(0, 15) == 0.0 and confidence_from_n(15, 15) == 0.5 and confidence_from_n(45, 15) == 0.75


def test_decay_and_percentile():
    assert decay_weight(0, 0.3) == 1.0 and decay_weight(2, 0.3) == pytest.approx(math.exp(-0.6))
    assert percentile_rank(3, [1, 2, 3, 4, 5]) == 50.0 and percentile_rank(5, [1, 2, 3, 4, 5]) == 90.0
    a, b, r2, _ = ols([1, 2, 3, 4], [3, 5, 7, 9])
    assert (a, b, r2) == pytest.approx((1.0, 2.0, 1.0))


# --------------------------------------------------------------------------- pipeline
def test_thin_sample_is_pulled_toward_department_level_prior():
    corpus = [section(f"big{i}", gpa=3.0, rating=3.5, n_g=500, n_s=200) for i in range(6)]
    corpus.append(section("thin", gpa=3.0, rating=5.0, n_g=500, n_s=3))       # 3 raves, same grades
    corpus.append(section("thick", gpa=3.0, rating=5.0, n_g=500, n_s=300))    # 300 raves
    scores, _ = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    thin = next(c for c in scores["thin"].components if c.name == "sentiment_rating_residual")
    thick = next(c for c in scores["thick"].components if c.name == "sentiment_rating_residual")
    assert thin.raw == 5.0 and thin.adjusted < thick.adjusted           # shrinkage bites the thin one
    assert scores["thin"].confidence < scores["thick"].confidence
    assert scores["thin"].confidence_by_source["sentiment"] == pytest.approx(3 / (3 + 15))
    assert "prior" in thin.detail and "m=15" in thin.detail


def test_recency_decay_reduces_effective_n_and_confidence():
    new = section("new", when=AS_OF, n_s=30)
    old = replace(section("old", when=date(2016, 6, 1), n_s=30), grades=[grades(3.0, 100, when=date(2016, 6, 1))])
    scores, _ = score_corpus([new, old] + [section(f"f{i}") for i in range(4)], ScoringConfig(as_of=AS_OF))
    assert scores["old"].effective_n["sentiment"] == pytest.approx(30 * math.exp(-0.3 * ((AS_OF - date(2016, 6, 1)).days / 365.25)), rel=1e-3)
    assert scores["new"].effective_n["sentiment"] == pytest.approx(30.0)
    assert scores["old"].confidence < scores["new"].confidence


def test_lenient_grader_scores_neutral_after_leniency_adjustment():
    """Corpus where rating is entirely explained by GPA: rating = 1 + 0.8*GPA. A lenient grader with a
    high raw rating sits exactly on that line -> quality ~ 0; a tough grader rated above the line is positive."""
    rng = random.Random(1)
    corpus = []
    for i in range(40):
        gpa = rng.uniform(2.2, 3.8)
        corpus.append(section(f"s{i}", gpa=gpa, rating=1 + 0.8 * gpa, n_g=2000, n_s=2000))
    corpus.append(section("lenient", gpa=3.9, rating=1 + 0.8 * 3.9, n_g=2000, n_s=2000))    # 4.12 raw: high, but earned by grades
    corpus.append(section("tough", gpa=2.4, rating=1 + 0.8 * 2.4 + 0.6, n_g=2000, n_s=2000))  # 3.52 raw: lower, but above the line
    scores, model = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    assert model is not None and model.r_squared > 0.9 and model.slope == pytest.approx(0.8, abs=0.1)   # one deliberate +0.6 outlier in 42 costs ~6% of variance
    lenient_rating = next(c for c in scores["lenient"].components if c.name == "sentiment_rating_residual")
    assert lenient_rating.raw > 4.0                                   # high raw rating...
    assert abs(scores["lenient"].quality) < 0.35                       # ...neutral after adjustment
    assert scores["tough"].quality > 1.0                               # rewarded for beating the leniency line
    assert "leniency model predicts" in lenient_rating.detail and "R²" in lenient_rating.detail


def test_r_squared_reports_how_much_leniency_explains():
    rng = random.Random(2)
    corpus = [section(f"s{i}", gpa=rng.uniform(2.2, 3.8), rating=rng.uniform(2.0, 5.0), n_g=2000, n_s=2000) for i in range(60)]
    _, model = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    assert model.n_sections == 60 and 0.0 <= model.r_squared < 0.3    # ratings independent of grades -> little explained


def test_sentiment_weight_zero_matches_corpus_without_sentiment():
    corpus = [section(f"s{i}", gpa=2.5 + 0.1 * i, rating=5.0 - 0.1 * i, dfw=0.05 * i, syllabus=SyllabusFeatures(0.5, i % 2 == 0, i % 3 == 0)) for i in range(8)]
    stripped = [replace(s, sentiment=[]) for s in corpus]
    cfg = ScoringConfig(as_of=AS_OF, weights=SourceWeights(grades=1.0, sentiment=0.0, syllabus=0.5))
    a, model_a = score_corpus(corpus, cfg)
    b, model_b = score_corpus(stripped, cfg)
    assert model_a is None and model_b is None
    for sid in a:
        assert a[sid].quality == pytest.approx(b[sid].quality) and a[sid].difficulty == b[sid].difficulty
        assert a[sid].confidence == pytest.approx(b[sid].confidence)
        assert not any(c.source == "sentiment" for c in a[sid].components)
        assert "sentiment" not in a[sid].confidence_by_source
    assert any(c.source == "grades" and c.axis == "difficulty" for c in a["s3"].components)


def test_difficulty_is_percentile_within_department_and_comparable_across_fields():
    corpus = []
    for i in range(10):
        corpus.append(section(f"cs{i}", dept="CS", gpa=3.6 - 0.1 * i, dfw=0.02 * i, n_g=1000))       # CS grades run high
        corpus.append(section(f"phys{i}", dept="PHYS", gpa=3.0 - 0.1 * i, dfw=0.05 + 0.03 * i, n_g=1000))  # physics harsher overall
    scores, _ = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    cs = [scores[f"cs{i}"].difficulty for i in range(10)]
    ph = [scores[f"phys{i}"].difficulty for i in range(10)]
    assert cs == sorted(cs) and ph == sorted(ph)                       # monotone in harshness
    assert cs == pytest.approx(ph)                                     # same rank in own field -> same percentile
    assert cs[0] == 5.0 and cs[-1] == 95.0


def test_no_data_section_is_prior_with_zero_confidence_not_rounded():
    corpus = [section(f"s{i}") for i in range(5)] + [SectionInput("empty", "CS", 100)]
    scores, _ = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    e = scores["empty"]
    assert e.confidence == 0.0 and e.effective_n == {"grades": 0.0, "sentiment": 0.0, "syllabus": 0.0}
    assert any("prior" in n for n in e.notes)
    tiny = section("tiny", n_g=1, n_s=1)
    scores, _ = score_corpus(corpus + [tiny], ScoringConfig(as_of=AS_OF))
    assert 0 < scores["tiny"].confidence < 0.1                         # small, but present


def test_components_are_complete_and_readable():
    corpus = [section(f"s{i}", syllabus=SyllabusFeatures(exam_weight=0.6, attendance_required=True, recorded_lectures=False)) for i in range(5)]
    scores, _ = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    names = {c.name for c in scores["s0"].components}
    assert {"sentiment_rating_residual", "sentiment_would_take_again_residual", "syllabus_recorded_lectures", "syllabus_attendance_required",
            "grade_dfw_rate", "grade_mean_gpa_inverted", "sentiment_difficulty", "syllabus_exam_weight",
            "grades_effective_n", "sentiment_effective_n", "syllabus_effective_n"} <= names
    for c in scores["s0"].components:
        assert c.detail and c.source in ("grades", "sentiment", "syllabus") and c.axis in ("quality", "difficulty", "confidence")
        assert isinstance(c.weight, float) and isinstance(c.contribution, float)
    q = scores["s0"]
    assert q.quality == pytest.approx(sum(c.contribution for c in q.components if c.axis == "quality"))


def test_purity_and_determinism():
    corpus = [section(f"s{i}", gpa=2.5 + 0.1 * i, rating=3 + 0.1 * i) for i in range(8)]
    a, ma = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    b, mb = score_corpus(corpus, ScoringConfig(as_of=AS_OF))
    assert a == b and ma == mb
    # as_of defaults to the latest date in the corpus, never the wall clock
    c, _ = score_corpus(corpus, ScoringConfig())
    assert {k: v.confidence for k, v in c.items()} == {k: v.confidence for k, v in a.items()}


def test_configurable_m_and_lambda():
    corpus = [section(f"s{i}", rating=3.5, n_s=200) for i in range(6)] + [section("x", rating=5.0, n_s=10)]
    strong, _ = score_corpus(corpus, ScoringConfig(as_of=AS_OF, prior_strength_m=100))
    weak, _ = score_corpus(corpus, ScoringConfig(as_of=AS_OF, prior_strength_m=1))
    def adj(sc): return next(c for c in sc["x"].components if c.name == "sentiment_rating_residual").adjusted
    assert adj(strong) < adj(weak)
    assert strong["x"].confidence_by_source["sentiment"] == pytest.approx(10 / 110)
