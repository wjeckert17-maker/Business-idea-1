from __future__ import annotations

import json

from ..models import InstitutionRef
from ..store import Store
from .names import parse_name


def run(store: Store, args) -> int:
    if args.cmd == "people-ingest":
        from .mentions import get_mention_adapter
        cls = get_mention_adapter(args.source)
        store.ensure_source(cls.code, cls.code, cls.kind, cls.base_trust)
        inst = InstitutionRef("people", args.institution, args.institution)
        run_id = store.start_run(cls.code, {"path": args.path, "institution": args.institution})
        n = dup = 0
        for m in cls(args.path, inst).records():
            if store.add_mention(m, run_id, vars(parse_name(m.raw_name))) is None:
                dup += 1
            else:
                n += 1
        store.commit(); store.finish_run(run_id, {"mentions": n, "duplicates": dup})
        print(json.dumps({"mentions": n, "duplicates": dup})); return 0
    if args.cmd == "people-resolve":
        from .resolver import resolve
        print(json.dumps(resolve(store, args.threshold, args.margin), indent=1)); return 0
    if args.cmd == "people-explain":
        from .resolver import explain_mention
        print(json.dumps(explain_mention(store, args.mention_id), indent=1, default=str)); return 0
    if args.cmd == "people-correct":
        cid = store.add_correction(args.author, "person", args.action, args.note, mention_ids=args.mention_ids)
        print("correction", cid, "recorded; run `canon people-resolve` to apply"); return 0
    if args.cmd == "people-evaluate":
        from .evaluate import evaluate, render
        store.ensure_source("sis", "SIS", "sis", 1.0); store.ensure_source("grade_feed", "grade feed", "grade_feed", 0.8)
        store.ensure_source("rating_site", "rating site", "rating_site", 0.5); store.ensure_source("syllabus", "syllabus", "syllabus", 0.7)
        rep = evaluate(store, args.path, InstitutionRef("people", args.institution, args.institution))
        md = render(rep); open(args.report, "w").write(md); print(md); return 0
    return 1
