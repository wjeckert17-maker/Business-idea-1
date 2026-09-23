from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date


def _jsonl(path):
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evalx", description="Evaluation harness: requirements, schedules, scoring sanity, replays")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("requirements"); s.add_argument("--transcripts", required=True); s.add_argument("--audits", required=True); s.add_argument("--trees", required=True, help="JSON {catalog_year: tree}"); s.add_argument("--out", default="eval_requirements")
    s = sub.add_parser("schedules"); s.add_argument("--corpus", required=True, help="scored corpus JSON (e.g. web/data/demo.json shape)"); s.add_argument("--registrations", required=True); s.add_argument("--out", default="eval_schedules")
    s = sub.add_parser("sanity"); s.add_argument("--corpus", required=True); s.add_argument("--demographics"); s.add_argument("--out", default="eval_sanity")
    s = sub.add_parser("replay"); s.add_argument("--corpus", required=True); s.add_argument("--version", required=True); s.add_argument("--out-dir", default="runs")
    s.add_argument("--m", type=float, default=15.0); s.add_argument("--decay", type=float, default=0.3); s.add_argument("--sentiment-weight", type=float, default=1.0)
    s.add_argument("--syllabus-weight", type=float, default=0.5); s.add_argument("--as-of"); s.add_argument("--k", type=int, default=5); s.add_argument("--time-limit", type=float, default=5.0)
    s = sub.add_parser("diff"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--out")
    s = sub.add_parser("fixture"); s.add_argument("--demo", required=True); s.add_argument("--out-dir", default="fixtures"); s.add_argument("--students", type=int, default=20); s.add_argument("--sections", type=int, default=60)
    args = p.parse_args(argv)

    if args.cmd == "requirements":
        from .requirements_eval import compare, render
        r = compare(_jsonl(args.transcripts), _jsonl(args.audits), json.load(open(args.trees)))
        json.dump(r.to_dict(), open(args.out + ".json", "w"), indent=1); open(args.out + ".md", "w").write(render(r)); print(render(r))
        return 0 if r.release_ok else 1
    if args.cmd == "schedules":
        from .schedule_eval import evaluate, render
        r = evaluate(json.load(open(args.corpus)), _jsonl(args.registrations))
        json.dump(r.to_dict(), open(args.out + ".json", "w"), indent=1); open(args.out + ".md", "w").write(render(r)); print(render(r)); return 0
    if args.cmd == "sanity":
        from .replay import _inputs
        from .scoring_sanity import render, run
        corpus = json.load(open(args.corpus))
        demo = json.load(open(args.demographics)) if args.demographics else corpus.get("demographics")
        r = run(_inputs(corpus), demographics=demo)
        json.dump(r.to_dict(), open(args.out + ".json", "w"), indent=1); open(args.out + ".md", "w").write(render(r)); print(render(r))
        return 0 if r.adjustment_ok else 1
    if args.cmd == "replay":
        from scoring import ScoringConfig, SourceWeights
        from .replay import replay, save
        from .scoring_sanity import render as rs
        from .schedule_eval import render as rsch, ScheduleReport
        cfg = ScoringConfig(prior_strength_m=args.m, decay_lambda=args.decay, as_of=date.fromisoformat(args.as_of) if args.as_of else None,
                            weights=SourceWeights(1.0, args.sentiment_weight, args.syllabus_weight))
        run = replay(json.load(open(args.corpus)), args.version, cfg, k=args.k, time_limit=args.time_limit)
        path = save(run, args.out_dir)
        print(f"saved {path}\n"); print(json.dumps({k: run["schedules"][k] for k in ("students", "course_hit_rate", "section_hit_rate", "mean_my_gpa", "mean_their_gpa", "mean_my_conflicts", "mean_their_conflicts")}, indent=1))
        print("sanity:", run["scoring_sanity"]["verdict"]); return 0
    if args.cmd == "diff":
        from .replay import diff, render_diff
        dd = diff(json.load(open(args.a)), json.load(open(args.b)))
        if args.out: json.dump(dd, open(args.out, "w"), indent=1)
        print(render_diff(dd)); return 0
    if args.cmd == "fixture":
        from .fixtures import build_corpus, build_requirement_fixture
        os.makedirs(args.out_dir, exist_ok=True)
        corpus = build_corpus(args.demo, args.sections, args.students)
        json.dump(corpus, open(os.path.join(args.out_dir, "term_corpus.json"), "w"))
        t, a = build_requirement_fixture(corpus["requirement_tree"], corrupt_one=True)
        with open(os.path.join(args.out_dir, "transcripts.jsonl"), "w") as fh: fh.writelines(json.dumps(x) + "\n" for x in t)
        with open(os.path.join(args.out_dir, "audits.jsonl"), "w") as fh: fh.writelines(json.dumps(x) + "\n" for x in a)
        json.dump({"2026-27": corpus["requirement_tree"]}, open(os.path.join(args.out_dir, "trees.json"), "w"))
        with open(os.path.join(args.out_dir, "registrations.jsonl"), "w") as fh: fh.writelines(json.dumps(x) + "\n" for x in corpus["registrations"])
        print("fixtures written to", args.out_dir, "(SYNTHETIC students; one audit deliberately corrupted)"); return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
