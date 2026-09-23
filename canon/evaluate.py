"""Markdown rendering of a matcher evaluation."""
from __future__ import annotations

import json
from typing import Dict


def _m(m: Dict) -> str:
    return f"P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f} (tp={m['tp']} fp={m['fp']} fn={m['fn']}, unverifiable={m['predicted_unverifiable']})"


def render(report: Dict, train_stats: Dict, model_id: int, embedder: str, ablation: Dict = None) -> str:
    L = [f"# Matcher evaluation — model {model_id} ({embedder})", ""]
    L += ["## Protocol", "",
          "* Ground truth pairs were split 80/20 by a salted hash of the directed pair key before any training.",
          "* Negatives exist only inside coverage universes (ASSIST agreements, SCNS/TCCNS institution sets) or explicit denials.",
          "  A pair the ground truth is silent about is 'unknown': never trained on, never counted in precision.",
          "* Threshold chosen on a validation slice of the *train* split to reach the target precision; holdout numbers below are untouched by that choice.",
          "* Recall counts holdout positives the candidate generator never surfaced as false negatives.", ""]
    L += ["## Training", "", f"* queries: {train_stats['queries']}, labeled train pairs: {train_stats['train_pairs']} (target precision {train_stats['target_precision']})"]
    for fam, d in train_stats["families"].items():
        if "skipped" in d:
            L.append(f"* {fam} targets: skipped — {d['skipped']}")
        else:
            met = "met" if d["target_met_on_validation"] else "**NOT MET**"
            L.append(f"* {fam} targets: fit {d['fit_pos']} pos / {d['fit_neg']} neg; threshold {d['threshold']} (target {met}); validation {_m(d['val'])}")
    L.append("")
    a = report["at_threshold"]
    L += ["## Holdout", "", f"* holdout pairs scored: {report['holdout_pairs_scored']}; positives missed by candidate generation: {report['holdout_positives_not_candidates']}",
          f"* **at the per-family thresholds {report['threshold']}: {_m(a)}**", ""]
    for fam, m in report.get("per_family", {}).items():
        L.append(f"* {fam} targets: {_m(m)}")
    L += ["", "| threshold (both families) | precision | recall | F1 | tp | fp | fn |", "|---|---|---|---|---|---|---|"]
    for t, m in report["sweep"].items():
        L.append(f"| {t} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | {m['tp']} | {m['fp']} | {m['fn']} |")
    L += ["", "### Per ground-truth source (holdout, at threshold)", "", "| source | precision | recall | tp | fp | fn |", "|---|---|---|---|---|---|"]
    for s, m in report["per_source"].items():
        L.append(f"| {s} | {m['precision']:.3f} | {m['recall']:.3f} | {m['tp']} | {m['fp']} | {m['fn']} |")
    if ablation:
        L += ["", "## Ablation: text-only (course-number and subject features disabled)", "",
              f"* thresholds {ablation['threshold']}: {_m(ablation['at_threshold'])}", ""]
        for fam, m in ablation.get("per_family", {}).items():
            L.append(f"* {fam} targets: {_m(m)}")
        L += ["", "| source | precision | recall | tp | fp | fn |", "|---|---|---|---|---|---|"]
        for s, m in ablation["per_source"].items():
            L.append(f"| {s} | {m['precision']:.3f} | {m['recall']:.3f} | {m['tp']} | {m['fp']} | {m['fn']} |")
    L += ["", "## Worst false positives (holdout)", ""]
    for fp in report["false_positives"][:15]:
        top = sorted(fp["contributions"].items(), key=lambda kv: -abs(kv[1]))[:4]
        L.append(f"* {fp['prob']:.3f}  {fp['from']}  →  {fp['to']}  [{', '.join(fp['sources'])}]  drivers: " + ", ".join(f"{k}={v:+.2f}" for k, v in top))
    L += ["", "## Lowest-scoring false negatives (holdout)", ""]
    for fn in report["false_negatives"][:10]:
        top = sorted(fn["contributions"].items(), key=lambda kv: kv[1])[:3]
        L.append(f"* {fn['prob']:.3f}  {fn['from']}  →  {fn['to']}  [{', '.join(fn['sources'])}]  drags: " + ", ".join(f"{k}={v:+.2f}" for k, v in top))
    if report.get("missed_by_candidate_generation"):
        L += ["", "## Positives never surfaced as candidates (sample)", ""]
        for m in report["missed_by_candidate_generation"]:
            L.append(f"* {m['from']}  →  {m['to']}  [{', '.join(m['sources'])}]")
    return "\n".join(L) + "\n"
