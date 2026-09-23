from __future__ import annotations

import argparse
import json
import sys

from . import extract
from .courses import CourseTable
from .llm import AnthropicRunner, DryRun, DryRunRunner, ReplayRunner
from .prompt import show
from .validate import Validator, finish
from .emit import emit


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="reqx", description="catalog requirements -> requirement tree JSON")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("outline", help="step 1 only: print the extracted hierarchy"); s.add_argument("input"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("show-prompt", help="print exactly what would be sent for one section"); s.add_argument("input"); s.add_argument("--section", type=int, default=0)
    s = sub.add_parser("courses", help="build a Course table JSON from CourseLeaf subject pages"); s.add_argument("directory"); s.add_argument("--out", required=True)
    s = sub.add_parser("run", help="extract -> model -> validate -> emit"); s.add_argument("input")
    s.add_argument("--courses", required=True, help="Course table JSON (see `courses`) or postgres://DSN")
    s.add_argument("--institution", help="institution slug when --courses is a DSN"); s.add_argument("--catalog-year", default="unknown")
    s.add_argument("--program-code", default="PROGRAM")
    s.add_argument("--runner", choices=["anthropic", "replay", "dry-run"], default="anthropic"); s.add_argument("--cache-dir", default="reqx_cache")
    s.add_argument("--out", default="requirements.json"); s.add_argument("--report", default="requirements_report.md")
    args = p.parse_args(argv)

    if args.cmd == "courses":
        t = CourseTable.from_courseleaf_dir(args.directory); t.to_json(args.out); print(len(t), "courses ->", args.out); return 0
    outline = extract.load(args.input)
    if args.cmd == "outline":
        print(json.dumps(outline.to_dict(), indent=1) if args.json else "\n\n".join(extract.render_group(g) for g in outline.groups()))
        print(f"\n[{len(outline.groups())} sections; stated total units: {outline.stated_total_units}]", file=sys.stderr); return 0
    if args.cmd == "show-prompt":
        g = outline.groups()[args.section]
        print(show(outline.program_title, outline.stated_total_units, extract.render_group(g), outline.preamble)); return 0

    courses = CourseTable.from_postgres(args.courses, args.institution) if args.courses.startswith("postgres") else CourseTable.from_json(args.courses)
    runner = {"anthropic": lambda: AnthropicRunner(args.cache_dir), "replay": lambda: ReplayRunner(args.cache_dir), "dry-run": DryRunRunner}[args.runner]()
    v = Validator(courses, outline)
    results, findings = [], []
    for g in outline.groups():
        text = extract.render_group(g)
        try:
            out = runner.run(outline.program_title, outline.stated_total_units, text, outline.preamble)
        except DryRun:
            print(f"\n--- dry run: {len(outline.groups())} sections would be sent; stopping after the first ---"); return 0
        gr, fs = v.validate(g, out)
        results.append(gr); findings.extend(fs)
        print(f"[{g.title}] model {gr.model_confidence:.2f} -> {gr.confidence:.2f} {'REVIEW' if gr.needs_review else 'ok'} credits {gr.credits_min}-{gr.credits_max} stated {gr.stated_units}", file=sys.stderr)
    report = finish(outline, results, findings)
    json.dump(emit(report, args.program_code, args.catalog_year), open(args.out, "w"), indent=1)
    open(args.report, "w").write(render_report(report))
    print(render_report(report))
    return 0 if report.ok_to_load else 1


def render_report(r) -> str:
    L = [f"# {r.program_title}", "", f"* stated total units: {r.stated_total_units}; tree yields {r.computed_total_min}-{r.computed_total_max}",
         f"* loadable without human review: **{'yes' if r.ok_to_load else 'no'}**", "", "| section | model conf | final conf | review | credits | stated |", "|---|---|---|---|---|---|"]
    for g in r.groups:
        L.append(f"| {g.title} | {g.model_confidence:.2f} | {g.confidence:.2f} | {'REVIEW: ' + '; '.join(g.review_reasons)[:120] if g.needs_review else 'ok'} | {g.credits_min}-{g.credits_max} | {g.stated_units or ''} |")
    L += ["", "## Findings", ""]
    for f in r.findings:
        L.append(f"* **{f.severity}** [{f.group}] {f.code}: {f.message}" + (f" (at {f.node_path})" if f.node_path else ""))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
