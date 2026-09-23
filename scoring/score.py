"""The pipeline, in the order specified:
  1. decay every observation, giving decayed means and effective sample sizes
  2. shrink each thin statistic toward its department+level prior, using the effective n
  3. fit leniency (shrunk rating ~ shrunk mean GPA) across the corpus; quality = residual
  4. difficulty = weighted standardized DFW / GPA / (difficulty rating) / (exam weight), percentile within department
Everything is a pure function of (sections, config). No I/O, no clock: `as_of` defaults to the latest date present.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from typing import Dict, List, Optional, Tuple

from .config import ScoringConfig
from .models import Component, LeniencyModel, SectionInput, SectionScore
from .stats import (confidence_from_n, decay_weight, decayed_mean, mean_sd, ols, percentile_rank, shrink, weighted_mean,
                    years_between, zscore)

STATS = ("gpa", "dfw", "rating", "difficulty", "wta")   # the shrinkable statistics
SOURCE_OF = {"gpa": "grades", "dfw": "grades", "rating": "sentiment", "difficulty": "sentiment", "wta": "sentiment"}


@dataclass
class _Raw:
    """Decayed observed statistics for one section."""
    mean: Dict[str, Optional[float]]
    n_eff: Dict[str, float]           # per stat
    source_n_eff: Dict[str, float]    # per source


# ---------------------------------------------------------------------------
def score_corpus(sections: List[SectionInput], config: ScoringConfig = ScoringConfig()) -> Tuple[Dict[str, SectionScore], Optional[LeniencyModel]]:
    """Score every section in the corpus. Returns ({section_id: SectionScore}, leniency model or None)."""
    if not sections:
        return {}, None
    w = config.weights
    sentiment_on = w.sentiment > 0
    as_of = config.as_of or _latest_date(sections)
    raw = {s.section_id: _decay(s, config, as_of, sentiment_on) for s in sections}
    priors = _priors(sections, raw, sentiment_on)

    # 2. shrinkage
    shrunk: Dict[str, Dict[str, float]] = {}
    prior_used: Dict[str, Dict[str, Tuple[float, str]]] = {}
    for s in sections:
        r = raw[s.section_id]
        shrunk[s.section_id], prior_used[s.section_id] = {}, {}
        for stat in STATS:
            if SOURCE_OF[stat] == "sentiment" and not sentiment_on:
                continue
            mu, scope = priors.lookup(stat, s.department, s.level)
            if mu is None:
                continue
            shrunk[s.section_id][stat] = shrink(r.mean.get(stat), r.n_eff.get(stat, 0.0), mu, config.prior_strength_m)
            prior_used[s.section_id][stat] = (mu, scope)

    # 3. leniency: regress shrunk rating on shrunk GPA over sections that have both signals observed
    leniency = None
    if sentiment_on:
        xs, ys = [], []
        for s in sections:
            r = raw[s.section_id]
            if r.n_eff.get("rating", 0) > 0 and r.n_eff.get("gpa", 0) > 0 and "rating" in shrunk[s.section_id] and "gpa" in shrunk[s.section_id]:
                xs.append(shrunk[s.section_id]["gpa"]); ys.append(shrunk[s.section_id]["rating"])
        if len(xs) >= 3:
            a, b, r2, sd = ols(xs, ys)
            leniency = LeniencyModel(a, b, r2, len(xs), sd)
    wta_model = None
    if sentiment_on:
        xs, ys = [], []
        for s in sections:
            r = raw[s.section_id]
            if r.n_eff.get("wta", 0) > 0 and r.n_eff.get("gpa", 0) > 0 and "wta" in shrunk[s.section_id] and "gpa" in shrunk[s.section_id]:
                xs.append(shrunk[s.section_id]["gpa"]); ys.append(shrunk[s.section_id]["wta"])
        if len(xs) >= 3:
            a, b, r2, sd = ols(xs, ys)
            wta_model = LeniencyModel(a, b, r2, len(xs), sd)

    # per-section component values (before standardization)
    qvals: Dict[str, Dict[str, float]] = defaultdict(dict)
    dvals: Dict[str, Dict[str, float]] = defaultdict(dict)
    for s in sections:
        sh = shrunk[s.section_id]
        if sentiment_on and "rating" in sh:
            qvals[s.section_id]["sentiment_rating_residual"] = sh["rating"] - (leniency.predict(sh["gpa"]) if (leniency and "gpa" in sh) else priors.lookup("rating", s.department, s.level)[0])
        if sentiment_on and "wta" in sh:
            qvals[s.section_id]["sentiment_would_take_again_residual"] = sh["wta"] - (wta_model.predict(sh["gpa"]) if (wta_model and "gpa" in sh) else priors.lookup("wta", s.department, s.level)[0])
        if w.syllabus > 0 and s.syllabus:
            if s.syllabus.recorded_lectures is not None:
                qvals[s.section_id]["syllabus_recorded_lectures"] = 1.0 if s.syllabus.recorded_lectures else 0.0
            if s.syllabus.attendance_required is not None:
                qvals[s.section_id]["syllabus_attendance_required"] = 1.0 if s.syllabus.attendance_required else 0.0
            if s.syllabus.exam_weight is not None:
                dvals[s.section_id]["syllabus_exam_weight"] = float(s.syllabus.exam_weight)
        if w.grades > 0:
            if "dfw" in sh:
                dvals[s.section_id]["grade_dfw_rate"] = sh["dfw"]
            if "gpa" in sh:
                dvals[s.section_id]["grade_mean_gpa_inverted"] = 4.0 - sh["gpa"]
        if sentiment_on and "difficulty" in sh:
            dvals[s.section_id]["sentiment_difficulty"] = sh["difficulty"]

    # standardization: quality across the corpus, difficulty within department
    q_stats = {name: mean_sd([v[name] for v in qvals.values() if name in v]) for name in config.quality_components}
    dept_of = {s.section_id: s.department for s in sections}
    d_stats: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for name in config.difficulty_components:
        by_dept: Dict[str, List[float]] = defaultdict(list)
        for sid, v in dvals.items():
            if name in v:
                by_dept[dept_of[sid]].append(v[name])
        for dept, xs in by_dept.items():
            d_stats[(dept, name)] = mean_sd(xs)

    # raw difficulty index per section, then percentile within department
    d_index: Dict[str, float] = {}
    for s in sections:
        total = 0.0
        for name, cw in config.difficulty_components.items():
            v = dvals[s.section_id].get(name)
            if v is None:
                continue
            src_w = _source_weight(name, w)
            if src_w <= 0:
                continue
            mu, sd = d_stats[(s.department, name)]
            total += cw * src_w * zscore(v, mu, sd)
        d_index[s.section_id] = total
    dept_pop: Dict[str, List[float]] = defaultdict(list)
    for sid, v in d_index.items():
        dept_pop[dept_of[sid]].append(v)

    out: Dict[str, SectionScore] = {}
    for s in sections:
        sid, r, sh = s.section_id, raw[s.section_id], shrunk[s.section_id]
        comps: List[Component] = []
        notes: List[str] = []
        m = config.prior_strength_m
        # --- quality
        quality = 0.0
        for name, cw in config.quality_components.items():
            src = name.split("_")[0]
            src_w = _source_weight(name, w)
            v = qvals[sid].get(name)
            if src_w <= 0 or v is None:
                continue
            mu, sd = q_stats[name]
            z = zscore(v, mu, sd)
            contrib = cw * src_w * z
            quality += contrib
            comps.append(_quality_component(name, src, cw * src_w, v, z, contrib, s, r, sh, prior_used[sid], leniency if name.startswith("sentiment_rating") else wta_model, m))
        # --- difficulty
        pop = dept_pop[s.department]
        if len(pop) >= config.min_dept_sections_for_percentile:
            difficulty = percentile_rank(d_index[sid], pop)
        else:
            difficulty = 50.0
            notes.append(f"department {s.department} has {len(pop)} scored section(s); percentile undefined, reported as 50")
        for name, cw in config.difficulty_components.items():
            src_w = _source_weight(name, w)
            v = dvals[sid].get(name)
            if src_w <= 0 or v is None:
                continue
            mu, sd = d_stats[(s.department, name)]
            z = zscore(v, mu, sd)
            comps.append(_difficulty_component(name, cw * src_w, v, z, cw * src_w * z, s, r, sh, prior_used[sid], m))
        # --- confidence: data share n/(n+m) per active source, combined by source weight
        conf_by_source: Dict[str, float] = {}
        eff: Dict[str, float] = {}
        active = {"grades": w.grades, "sentiment": w.sentiment, "syllabus": w.syllabus}
        for src, sw in active.items():
            if sw <= 0:
                continue
            if src == "syllabus":
                n = 1.0 if (s.syllabus and any(x is not None for x in (s.syllabus.exam_weight, s.syllabus.attendance_required, s.syllabus.recorded_lectures))) else 0.0
                c = 1.0 if n else 0.0     # a syllabus is present or it is not; it has no sample size
            else:
                n = r.source_n_eff.get(src, 0.0)
                c = confidence_from_n(n, m)
            conf_by_source[src] = c
            eff[src] = n
            comps.append(Component(f"{src}_effective_n", src, "confidence", n, c, n, sw, sw * c,
                                   f"{src}: effective sample size {n:.2f} after decay (λ={config.decay_lambda}); data share n/(n+m) = {c:.3f} with m={m}"))
        total_w = sum(sw for src, sw in active.items() if sw > 0)
        confidence = sum(active[src] * c for src, c in conf_by_source.items()) / total_w if total_w > 0 else 0.0
        if not any(n > 0 for n in eff.values()):
            notes.append("no observations for any active source: scores are the department/level prior, confidence 0")
        out[sid] = SectionScore(sid, quality, difficulty, confidence, conf_by_source, eff, comps, notes)
    return out, leniency


# ---------------------------------------------------------------------------
def _latest_date(sections: List[SectionInput]) -> date:
    dates = [g.term_date for s in sections for g in s.grades] + [o.observed_date for s in sections for o in s.sentiment]
    return max(dates) if dates else date(1970, 1, 1)


def _source_weight(component: str, w) -> float:
    return {"grade": w.grades, "sentiment": w.sentiment, "syllabus": w.syllabus}[component.split("_")[0]]


def _decay(s: SectionInput, config: ScoringConfig, as_of: date, sentiment_on: bool) -> _Raw:
    lam, pts = config.decay_lambda, config.gpa_points
    gpa_vals, dfw_vals = [], []
    for g in s.grades:
        wgt = decay_weight(years_between(as_of, g.term_date), lam)
        n_graded = g.graded_n()
        if n_graded > 0:
            gpa = g.mean_gpa if g.mean_gpa is not None else sum(pts.get(k, 0.0) * c for k, c in g.letter_counts.items()) / n_graded
            gpa_vals.append((gpa, n_graded, wgt))
        n_total = n_graded + g.w_count
        if n_total > 0:
            dfw = (sum(c for k, c in g.letter_counts.items() if k in config.dfw_letters) + g.w_count) / n_total
            dfw_vals.append((dfw, n_total, wgt))
    mean, n_eff = {}, {}
    mean["gpa"], n_eff["gpa"] = decayed_mean(gpa_vals)
    mean["dfw"], n_eff["dfw"] = decayed_mean(dfw_vals)
    src_n = {"grades": n_eff["dfw"]}
    if sentiment_on:
        for stat, attr in (("rating", "rating"), ("difficulty", "difficulty"), ("wta", "would_take_again")):
            vals = [(getattr(o, attr), o.n, decay_weight(years_between(as_of, o.observed_date), lam)) for o in s.sentiment if getattr(o, attr) is not None and o.n > 0]
            mean[stat], n_eff[stat] = decayed_mean(vals)
        src_n["sentiment"] = max(n_eff.get("rating", 0.0), n_eff.get("difficulty", 0.0), n_eff.get("wta", 0.0))
    return _Raw(mean, n_eff, src_n)


class _Priors:
    """Prior means per (department, level), falling back to department, then corpus. Each is the
    effective-n-weighted mean of the sections' decayed statistics."""

    def __init__(self):
        self.table: Dict[Tuple[str, Optional[str], Optional[int]], float] = {}

    def lookup(self, stat: str, dept: str, level: int) -> Tuple[Optional[float], str]:
        for key, scope in (((stat, dept, level), f"{dept} level {level}"), ((stat, dept, None), f"{dept} (all levels)"), ((stat, None, None), "corpus")):
            if key in self.table:
                return self.table[key], scope
        return None, "none"


def _priors(sections: List[SectionInput], raw: Dict[str, _Raw], sentiment_on: bool) -> _Priors:
    P = _Priors()
    for stat in STATS:
        if SOURCE_OF[stat] == "sentiment" and not sentiment_on:
            continue
        groups: Dict[Tuple[Optional[str], Optional[int]], List[Tuple[float, float]]] = defaultdict(list)
        for s in sections:
            r = raw[s.section_id]
            x, n = r.mean.get(stat), r.n_eff.get(stat, 0.0)
            if x is None or n <= 0:
                continue
            for key in ((s.department, s.level), (s.department, None), (None, None)):
                groups[key].append((x, n))
        for (dept, level), pairs in groups.items():
            mu = weighted_mean(pairs)
            if mu is not None:
                P.table[(stat, dept, level)] = mu
    return P


def _quality_component(name, src, weight, value, z, contrib, s, r, sh, prior_used, model, m) -> Component:
    if name.startswith("sentiment"):
        stat = "rating" if "rating" in name else "wta"
        rawv, n = r.mean.get(stat), r.n_eff.get(stat, 0.0)
        mu, scope = prior_used[stat]
        pred = model.predict(sh["gpa"]) if (model and "gpa" in sh) else mu
        basis = (f"leniency model predicts {pred:.3f} from shrunk GPA {sh['gpa']:.2f} (R²={model.r_squared:.2f})" if (model and "gpa" in sh)
                 else f"no leniency model / no GPA: compared to prior {mu:.3f}")
        detail = (f"observed {stat} {_f(rawv)} over n_eff {n:.1f}; shrunk toward {scope} prior {mu:.3f} with m={m} → {sh[stat]:.3f}; "
                  f"{basis}; residual {value:+.3f} = z {z:+.2f}; weight {weight:.2f} → contribution {contrib:+.3f}")
        return Component(name, "sentiment", "quality", rawv, value, n, weight, contrib, detail)
    label = name.replace("syllabus_", "").replace("_", " ")
    return Component(name, "syllabus", "quality", value, value, 1.0, weight, contrib,
                     f"syllabus states {label} = {bool(value)}; z {z:+.2f} vs corpus; weight {weight:.2f} → contribution {contrib:+.3f}")


def _difficulty_component(name, weight, value, z, contrib, s, r, sh, prior_used, m) -> Component:
    if name == "grade_dfw_rate":
        mu, scope = prior_used["dfw"]
        return Component(name, "grades", "difficulty", r.mean.get("dfw"), value, r.n_eff.get("dfw", 0.0), weight, contrib,
                         f"observed DFW rate {_f(r.mean.get('dfw'))} over n_eff {r.n_eff.get('dfw', 0):.1f}; shrunk toward {scope} prior {mu:.3f} (m={m}) → {value:.3f}; z {z:+.2f} within {s.department}; weight {weight:.2f} → {contrib:+.3f}")
    if name == "grade_mean_gpa_inverted":
        mu, scope = prior_used["gpa"]
        return Component(name, "grades", "difficulty", r.mean.get("gpa"), value, r.n_eff.get("gpa", 0.0), weight, contrib,
                         f"observed mean GPA {_f(r.mean.get('gpa'))} over n_eff {r.n_eff.get('gpa', 0):.1f}; shrunk toward {scope} prior {mu:.3f} (m={m}) → {sh['gpa']:.3f}; inverted (4 − GPA) = {value:.3f}; z {z:+.2f} within {s.department}; weight {weight:.2f} → {contrib:+.3f}")
    if name == "sentiment_difficulty":
        mu, scope = prior_used["difficulty"]
        return Component(name, "sentiment", "difficulty", r.mean.get("difficulty"), value, r.n_eff.get("difficulty", 0.0), weight, contrib,
                         f"observed difficulty rating {_f(r.mean.get('difficulty'))} over n_eff {r.n_eff.get('difficulty', 0):.1f}; shrunk toward {scope} prior {mu:.3f} (m={m}) → {value:.3f}; z {z:+.2f} within {s.department}; weight {weight:.2f} → {contrib:+.3f}")
    return Component(name, "syllabus", "difficulty", value, value, 1.0, weight, contrib,
                     f"syllabus exam weight {value:.2f}; z {z:+.2f} within {s.department}; weight {weight:.2f} → {contrib:+.3f}")


def _f(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.3f}"
