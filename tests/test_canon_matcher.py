"""Matcher building blocks on synthetic data: scorer, threshold policy, split determinism, embedder."""
import numpy as np

from canon.embed import HashingEmbedder
from canon.matcher import LinearScorer, ScoredPair, choose_threshold, metrics_at
from canon.split import split_of


def test_scorer_learns_and_explains():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 3))
    y = (X[:, 0] * 2 - X[:, 1] + rng.normal(scale=0.3, size=2000) > 0).astype(float)
    sc = LinearScorer.fit(X, y, ["a", "b", "c"])
    p = sc.score(X)
    assert ((p > 0.5) == (y > 0.5)).mean() > 0.9
    contrib = sc.contributions([2.0, 0.0, 0.0])
    assert contrib["a"] > 0 and abs(contrib["c"]) < abs(contrib["a"])
    sc2 = LinearScorer.fit(X, y, ["a", "b", "c"], disabled={"a"})
    assert sc2.w[0] == 0.0


def test_threshold_targets_precision_on_labeled_only():
    pairs = [ScoredPair(0, i, [], "pos" if i < 50 else "neg", set(), "train", prob=1 - i / 100) for i in range(100)]
    pairs[55].label = "unknown"; pairs[55].prob = 0.99   # unknown must not count against precision
    t, met = choose_threshold(pairs, 0.98)
    m = metrics_at([p for p in pairs if p.label != "unknown"], t)
    assert met and m["precision"] >= 0.98 and m["recall"] > 0.9
    assert metrics_at(pairs, t)["predicted_unverifiable"] == 1
    # an unreachable target is reported, not silently faked
    noisy = [ScoredPair(0, i, [], "pos" if i % 2 else "neg", set(), "train", prob=1 - i / 100) for i in range(100)]
    t2, met2 = choose_threshold(noisy, 0.98)
    assert not met2 and 0.5 <= t2 <= 0.995


def test_split_is_deterministic_and_directional():
    assert split_of(1, 2, "v1") == split_of(1, 2, "v1")
    kinds = {split_of(i, i + 1, "v1") for i in range(200)}
    assert kinds == {"train", "holdout"}
    assert any(split_of(i, j, "v1") != split_of(j, i, "v1") for i in range(50) for j in range(50) if i != j)


def test_hashing_embedder_orders_by_similarity():
    e = HashingEmbedder(dim=256).fit(["introduction to programming", "calculus i", "organic chemistry laboratory", "programming fundamentals"])
    E = e.encode(["Intro to Programming", "Programming Fundamentals", "Organic Chemistry Lab"])
    assert E[0] @ E[1] > E[0] @ E[2]
    assert abs(np.linalg.norm(E[0]) - 1) < 1e-5
    sp = e.sparse("Calculus I")
    assert HashingEmbedder.sparse_cos(sp, e.sparse("Calculus 1")) > 0.99
