"""Evaluation 3: is the leniency adjustment doing its job, and are adjusted scores skewed by instructor demographics?

  r(raw rating, mean GPA)        should be clearly positive (that is the leniency effect)
  r(adjusted quality, mean GPA)  should be near zero (the effect has been removed)
Demographics: optional {section_key: {"attribute": "group"}} — reported per attribute, per group, whether or not flattering.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from scoring import ScoringConfig, SectionInput, score_corpus

RAW_POS_MIN = 0.2      # below this, "leniency" barely exists in the data and the check is uninformative
ADJ_ABS_MAX = 0.1      # above this, the adjustment is not removing the grade effect


def pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


@dataclass
class SkewRow:
    attribute: str
    group: str
    n: int
    mean_quality: float
    mean_difficulty: float
    quality_delta_vs_rest: float
    p_value_quality: float           # permutation test on the quality delta (two-sided)


@dataclass
class SanityReport:
    sections_with_both: int
    r_raw_rating_vs_gpa: Optional[float]
    r_adjusted_quality_vs_gpa: Optional[float]
    r_would_take_again_raw_vs_gpa: Optional[float]
    leniency_r_squared: Optional[float]
    verdict: str
    adjustment_ok: bool
    skew: List[SkewRow]
    skew_note: str

    def to_dict(self): return asdict(self)


def run(inputs: List[SectionInput], config: ScoringConfig = ScoringConfig(), demographics: Optional[Dict[str, Dict[str, str]]] = None,
        seed: int = 0, permutations: int = 2000) -> SanityReport:
    scores, model = score_corpus(inputs, config)
    raw_rating, gpa, adj, wta = [], [], [], []
    for sid, sc in scores.items():
        comps = {c.name: c for c in sc.components}
        r = comps.get("sentiment_rating_residual"); g = comps.get("grade_mean_gpa_inverted")
        if r is None or g is None or r.raw is None or g.raw is None:
            continue
        raw_rating.append(r.raw); gpa.append(g.raw); adj.append(sc.quality)
        w = comps.get("sentiment_would_take_again_residual")
        wta.append(w.raw if (w and w.raw is not None) else None)
    r_raw = pearson(raw_rating, gpa)
    r_adj = pearson(adj, gpa)
    pairs = [(w, g) for w, g in zip(wta, gpa) if w is not None]
    r_wta = pearson([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 3 else None
    if r_raw is None or r_adj is None:
        verdict, ok = "not enough sections with both ratings and grades to test", False
    elif r_raw < RAW_POS_MIN:
        verdict, ok = f"raw rating barely tracks GPA (r={r_raw:.2f}); there is little leniency to remove, so this check is uninformative here", abs(r_adj) <= ADJ_ABS_MAX
    elif abs(r_adj) <= ADJ_ABS_MAX:
        verdict, ok = f"adjustment works: raw r={r_raw:.2f} → adjusted r={r_adj:.2f}", True
    else:
        verdict, ok = f"ADJUSTMENT NOT WORKING: adjusted quality still correlates with GPA (r={r_adj:.2f}; raw {r_raw:.2f})", False

    skew: List[SkewRow] = []
    note = "no demographic data supplied; skew audit not run"
    if demographics:
        rng = random.Random(seed)
        attrs = sorted({a for d in demographics.values() for a in d})
        labelled = [(sid, scores[sid]) for sid in scores if sid in demographics]
        note = f"skew audit over {len(labelled)} labelled sections" if labelled else "demographic labels did not match any scored section"
        for attr in attrs:
            groups = sorted({demographics[sid].get(attr) for sid, _ in labelled if demographics[sid].get(attr)})
            for g in groups:
                inside = [sc.quality for sid, sc in labelled if demographics[sid].get(attr) == g]
                rest = [sc.quality for sid, sc in labelled if demographics[sid].get(attr) not in (None, g)]
                diff_in = [sc.difficulty for sid, sc in labelled if demographics[sid].get(attr) == g]
                if not inside or not rest:
                    continue
                delta = sum(inside) / len(inside) - sum(rest) / len(rest)
                pool = inside + rest
                extreme = 0
                for _ in range(permutations):
                    rng.shuffle(pool)
                    d = sum(pool[:len(inside)]) / len(inside) - sum(pool[len(inside):]) / len(rest)
                    if abs(d) >= abs(delta):
                        extreme += 1
                skew.append(SkewRow(attr, g, len(inside), sum(inside) / len(inside), sum(diff_in) / len(diff_in), delta, (extreme + 1) / (permutations + 1)))
    return SanityReport(len(adj), r_raw, r_adj, r_wta, model.r_squared if model else None, verdict, ok, skew, note)


def render(r: SanityReport) -> str:
    f = lambda x: "n/a" if x is None else f"{x:+.3f}"
    L = ["# Scoring sanity", "", f"* sections with both ratings and grades: {r.sections_with_both}",
         f"* r(raw rating, mean GPA) = **{f(r.r_raw_rating_vs_gpa)}** (expected clearly positive)",
         f"* r(adjusted quality, mean GPA) = **{f(r.r_adjusted_quality_vs_gpa)}** (expected within ±{ADJ_ABS_MAX})",
         f"* r(raw would-take-again, mean GPA) = {f(r.r_would_take_again_raw_vs_gpa)}; leniency model R² = {f(r.leniency_r_squared)}",
         f"* verdict: **{r.verdict}** → {'OK' if r.adjustment_ok else 'BLOCK'}", "", f"## Demographic skew", "", f"* {r.skew_note}"]
    if r.skew:
        L += ["", "| attribute | group | n | mean adjusted quality | mean difficulty | Δ quality vs rest | permutation p |", "|---|---|---|---|---|---|---|"]
        L += [f"| {s.attribute} | {s.group} | {s.n} | {s.mean_quality:+.3f} | {s.mean_difficulty:.1f} | {s.quality_delta_vs_rest:+.3f} | {s.p_value_quality:.3f} |" for s in r.skew]
        flagged = [s for s in r.skew if s.p_value_quality < 0.05]
        L += ["", ("**Groups whose adjusted quality differs from the rest at p<0.05: " + ", ".join(f"{s.attribute}={s.group} ({s.quality_delta_vs_rest:+.2f})" for s in flagged) + "**") if flagged else "No group differs from the rest at p<0.05."]
    return "\n".join(L) + "\n"
