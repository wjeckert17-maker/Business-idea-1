"""Replay a past term against a scorer version; store versioned JSON; diff two runs.

A term corpus (JSON) holds everything a replay needs and nothing pre-scored:
  term, sections (crn, course_code, meetings, instructor, capacity, enrolled, credits, section_key),
  scoring_inputs {section_key: {department, level, grades:[...], sentiment:[...], syllabus:{...}}},
  courses, requirement_tree, registrations [...], demographics? {section_key: {...}}
`replay` scores the inputs with the given ScoringConfig, attaches the scores to the sections, then runs
the scoring-sanity and schedule-preference evaluations. The run record carries the config, the code
version and a hash of the corpus, so two runs are comparable only when those match.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from scoring import GradeObservation, ScoringConfig, SectionInput, SentimentObservation, SyllabusFeatures, score_corpus
from sched.models import Weights
from . import schedule_eval, scoring_sanity

CODE_VERSION = "evalx-0.1"


def _inputs(corpus: Dict[str, Any]) -> List[SectionInput]:
    out = []
    for key, v in corpus["scoring_inputs"].items():
        grades = [GradeObservation(date.fromisoformat(g["term_date"]), g["letter_counts"], g.get("w_count", 0), g.get("mean_gpa")) for g in v.get("grades", [])]
        sent = [SentimentObservation(date.fromisoformat(s["observed_date"]), s["n"], s.get("rating"), s.get("difficulty"), s.get("would_take_again")) for s in v.get("sentiment", [])]
        syl = SyllabusFeatures(**v["syllabus"]) if v.get("syllabus") else None
        out.append(SectionInput(key, v["department"], int(v["level"]), grades, sent, syl))
    return out


def score_sections(corpus: Dict[str, Any], config: ScoringConfig) -> Dict[str, Any]:
    """Corpus copy with quality/difficulty/confidence/history attached to every section."""
    inputs = _inputs(corpus)
    scores, _ = score_corpus(inputs, config)
    hist: Dict[str, Dict[str, Any]] = {}
    for inp in inputs:
        n = sum(g.graded_n() + g.w_count for g in inp.grades)
        graded = sum(g.graded_n() for g in inp.grades)
        pts = config.gpa_points
        gsum = sum((g.mean_gpa if g.mean_gpa is not None else (sum(pts.get(k, 0.0) * c for k, c in g.letter_counts.items()) / max(1, g.graded_n()))) * g.graded_n() for g in inp.grades)
        hist[inp.section_id] = {"grade_n": n, "grade_terms": len(inp.grades), "mean_gpa": (gsum / graded) if graded else None,
                                "w_rate": (sum(g.w_count for g in inp.grades) / n) if n else None}
    out = json.loads(json.dumps(corpus))
    for s in out["sections"]:
        sc = scores.get(s.get("section_key"))
        if sc:
            s.update({"quality": sc.quality, "difficulty": sc.difficulty, "confidence": sc.confidence, "history": hist.get(s["section_key"])})
        else:
            s.update({"quality": None, "difficulty": None, "confidence": 0.0, "history": None})
    return out


def corpus_hash(corpus: Dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(corpus, sort_keys=True).encode()).hexdigest()[:12]


def replay(corpus: Dict[str, Any], version: str, config: ScoringConfig = ScoringConfig(), weights: Optional[Weights] = None,
           k: int = 5, time_limit: float = 5.0) -> Dict[str, Any]:
    scored = score_sections(corpus, config)
    sanity = scoring_sanity.run(_inputs(corpus), config, corpus.get("demographics"))
    sched = schedule_eval.evaluate(scored, corpus.get("registrations", []), weights, k, time_limit)
    return {
        "meta": {"version": version, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_version": CODE_VERSION,
                 "scoring_config": _cfg(config), "weights": dataclasses.asdict(weights) if weights else None, "corpus_hash": corpus_hash(corpus),
                 "term": corpus.get("term")},
        "section_scores": {s["crn"]: {"quality": s["quality"], "difficulty": s["difficulty"], "confidence": s["confidence"]} for s in scored["sections"]},
        "scoring_sanity": sanity.to_dict(),
        "schedules": sched.to_dict(),
    }


def _cfg(c: ScoringConfig) -> Dict[str, Any]:
    d = dataclasses.asdict(c)
    d["as_of"] = c.as_of.isoformat() if c.as_of else None
    return d


def save(run: Dict[str, Any], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    stamp = run["meta"]["created_at"].replace(":", "").replace("-", "")
    path = os.path.join(out_dir, f"{run['meta']['version']}_{stamp}.json")
    json.dump(run, open(path, "w"), indent=1)
    return path


# -------------------------------------------------------------------------------
def spearman(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 3:
        return None
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for t in range(i, j + 1):
                r[order[t]] = (i + j) / 2 + 1
            i = j + 1
        return r
    return scoring_sanity.pearson(ranks(a), ranks(b))


def diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Better, worse, or just different? Compare aggregates, per-student outcomes and section-score ranking."""
    warn = []
    if a["meta"]["corpus_hash"] != b["meta"]["corpus_hash"]:
        warn.append("runs were made on different corpora; deltas are not comparable")
    A, B = a["schedules"], b["schedules"]
    sa = {s["student_id"]: s for s in A["per_student"]}; sb = {s["student_id"]: s for s in B["per_student"]}
    common = sorted(set(sa) & set(sb))
    top1_changed = [s for s in common if sa[s]["top1_crns"] != sb[s]["top1_crns"]]
    top5_changed = [s for s in common if sa[s]["top_course_sets"] != sb[s]["top_course_sets"]]
    gained = [s for s in common if not sa[s]["course_hit_rank"] and sb[s]["course_hit_rank"]]
    lost = [s for s in common if sa[s]["course_hit_rank"] and not sb[s]["course_hit_rank"]]
    crns = sorted(set(a["section_scores"]) & set(b["section_scores"]))
    qa = [a["section_scores"][c]["quality"] for c in crns if a["section_scores"][c]["quality"] is not None and b["section_scores"][c]["quality"] is not None]
    qb = [b["section_scores"][c]["quality"] for c in crns if a["section_scores"][c]["quality"] is not None and b["section_scores"][c]["quality"] is not None]
    rho = spearman(qa, qb)
    def d(key):
        x, y = A.get(key), B.get(key)
        return None if x is None or y is None else round(y - x, 4)
    agg = {"course_hit_rate": d("course_hit_rate"), "section_hit_rate": d("section_hit_rate"), "mean_my_gpa": d("mean_my_gpa"), "mean_my_conflicts": d("mean_my_conflicts")}
    sanity_delta = {"r_raw_rating_vs_gpa": _d(a["scoring_sanity"]["r_raw_rating_vs_gpa"], b["scoring_sanity"]["r_raw_rating_vs_gpa"]),
                    "r_adjusted_quality_vs_gpa": _d(a["scoring_sanity"]["r_adjusted_quality_vs_gpa"], b["scoring_sanity"]["r_adjusted_quality_vs_gpa"])}
    better = [k for k, v in agg.items() if v is not None and ((v > 0) if k in ("course_hit_rate", "section_hit_rate", "mean_my_gpa") else (v < 0))]
    worse = [k for k, v in agg.items() if v is not None and ((v < 0) if k in ("course_hit_rate", "section_hit_rate", "mean_my_gpa") else (v > 0))]
    if not top1_changed and not top5_changed and (rho is None or rho > 0.999):
        verdict = "no change: identical recommendations"
    elif not better and not worse:
        verdict = f"just different: {len(top1_changed)} of {len(common)} students get a different #1 but no aggregate moved"
    else:
        verdict = f"better on {', '.join(better) or 'nothing'}; worse on {', '.join(worse) or 'nothing'}; {len(top1_changed)} of {len(common)} students get a different #1"
    return {"a": a["meta"], "b": b["meta"], "warnings": warn, "aggregate_delta": agg, "sanity_delta": sanity_delta,
            "students_common": len(common), "top1_changed": top1_changed, "top5_changed": len(top5_changed), "hits_gained": gained, "hits_lost": lost,
            "section_quality_rank_correlation": rho, "verdict": verdict}


def _d(x, y):
    return None if x is None or y is None else round(y - x, 4)


def render_diff(dd: Dict[str, Any]) -> str:
    L = [f"# Replay diff: {dd['a']['version']} → {dd['b']['version']}", ""]
    L += [f"* WARNING: {w}" for w in dd["warnings"]]
    L += [f"* **{dd['verdict']}**", f"* section quality rank correlation between versions: {dd['section_quality_rank_correlation']}",
          f"* students in both runs: {dd['students_common']}; different #1: {len(dd['top1_changed'])}; different top-5 course sets: {dd['top5_changed']}",
          f"* course-set hits gained: {dd['hits_gained'] or '—'}; lost: {dd['hits_lost'] or '—'}", "", "| aggregate | Δ (b − a) |", "|---|---|"]
    L += [f"| {k} | {v if v is not None else 'n/a'} |" for k, v in dd["aggregate_delta"].items()]
    L += [f"| sanity {k} | {v if v is not None else 'n/a'} |" for k, v in dd["sanity_delta"].items()]
    return "\n".join(L) + "\n"
