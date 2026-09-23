from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .store import Store


def open_store(args) -> Store:
    if args.db.startswith("postgres"):
        return Store.postgres(args.db)
    return Store.sqlite(args.db)


def parse_ref(store: Store, text: str):
    """'San Jose State University | CS | 46A' or 'node:123' -> node row."""
    if text.startswith("node:"):
        rows = store.query("SELECT * FROM node WHERE node_id = ?", (int(text[5:]),))
        return rows[0] if rows else None
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3:
        raise SystemExit("node reference must be 'Institution name | SUBJ | NUMBER' or 'node:<id>'")
    return store.find_node(*parts)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="canon", description="Cross-institution course & instructor canonical graph")
    p.add_argument("--db", default=os.environ.get("CANON_DB", "canon.db"), help="sqlite path or postgres DSN")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    sub.add_parser("sources")
    s = sub.add_parser("ingest"); s.add_argument("--source", required=True); s.add_argument("--path", required=True)
    s.add_argument("--option", action="append", default=[], help="key=value adapter option (repeatable)")
    s = sub.add_parser("split"); s.add_argument("--salt", default="v1"); s.add_argument("--holdout-pct", type=int, default=20)
    s = sub.add_parser("train"); s.add_argument("--embedder", default="hashing", choices=["hashing", "minilm"])
    s.add_argument("--queries", type=int, default=20000); s.add_argument("--eval-queries", type=int, default=10000)
    s.add_argument("--target-precision", type=float, default=0.98); s.add_argument("--salt", default="v1")
    s.add_argument("--cache-dir", default="."); s.add_argument("--report", default="matcher_eval.md"); s.add_argument("--no-ablation", action="store_true")
    s = sub.add_parser("infer"); s.add_argument("--embedder", default="hashing", choices=["hashing", "minilm"]); s.add_argument("--model-id", type=int)
    s.add_argument("--scope"); s.add_argument("--limit", type=int); s.add_argument("--min-prob", type=float, default=0.5); s.add_argument("--cache-dir", default=".")
    s = sub.add_parser("resolve"); s.add_argument("--threshold", type=float)
    s = sub.add_parser("query"); s.add_argument("node"); s.add_argument("--as-of", help="catalog date YYYY-MM-DD"); s.add_argument("--system-at", help="ISO timestamp: what we believed then")
    s.add_argument("--min-confidence", type=float, default=0.0)
    s = sub.add_parser("explain"); s.add_argument("from_node"); s.add_argument("to_node")
    s = sub.add_parser("correct"); s.add_argument("--author", required=True); s.add_argument("--action", required=True, choices=["assert", "reject"])
    s.add_argument("from_node"); s.add_argument("to_node"); s.add_argument("--relation", default="satisfies"); s.add_argument("--confidence", type=float, default=1.0)
    s.add_argument("--effective-from"); s.add_argument("--effective-to"); s.add_argument("--note")
    s = sub.add_parser("people-ingest"); s.add_argument("--source", required=True); s.add_argument("--path", required=True); s.add_argument("--institution", required=True)
    s = sub.add_parser("people-resolve"); s.add_argument("--threshold", type=float, default=0.6); s.add_argument("--margin", type=float, default=0.15)
    s = sub.add_parser("people-explain"); s.add_argument("mention_id", type=int)
    s = sub.add_parser("people-correct"); s.add_argument("--author", required=True); s.add_argument("--action", required=True, choices=["must_link", "cannot_link"])
    s.add_argument("mention_ids", nargs="+", type=int); s.add_argument("--note")
    s = sub.add_parser("people-evaluate"); s.add_argument("--path", required=True); s.add_argument("--institution", required=True); s.add_argument("--report", default="people_eval.md")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    store = open_store(args)
    if args.cmd == "init":
        store.create_schema(); print("schema ready:", args.db); return 0
    if args.cmd == "sources":
        from .sources import all_adapters
        for code, cls in all_adapters().items():
            print(f"{code:8s} {cls.kind:16s} trust={cls.base_trust:<5} {cls.name}")
        return 0
    if args.cmd == "ingest":
        from .ingest import ingest
        opts = {}
        for kv in args.option:
            k, v = kv.split("=", 1)
            if k == "institutions":
                v = json.load(open(v))
            elif v.isdigit():
                v = int(v)
            opts[k] = v
        print(json.dumps(ingest(store, args.source, args.path, **opts), indent=1)); return 0
    if args.cmd == "split":
        from .split import assign
        print(json.dumps(assign(store, args.salt, args.holdout_pct))); return 0
    if args.cmd == "train":
        from .matcher import Matcher, CODE_FEATURES
        from .evaluate import render
        m = Matcher(store, args.embedder, cache_dir=args.cache_dir, salt=args.salt)
        train_stats = m.train(n_queries=args.queries, target_precision=args.target_precision)
        report = m.evaluate(n_queries=args.eval_queries)
        model_id = m.save_model(train_stats, report)
        ablation = None
        if not args.no_ablation:
            m2 = Matcher.__new__(Matcher); m2.__dict__.update(m.__dict__)
            m2.train(n_queries=args.queries, target_precision=args.target_precision, disabled=CODE_FEATURES)
            ablation = m2.evaluate(n_queries=args.eval_queries)
            store.execute("UPDATE matcher_model SET metrics = ? WHERE model_id = ?", (store._j({"train": train_stats, "holdout": report, "ablation_text_only": ablation}), model_id)); store.commit()
        md = render(report, train_stats, model_id, m.embedder.name, ablation)
        open(args.report, "w").write(md)
        print(md); print("model_id", model_id); return 0
    if args.cmd == "infer":
        from .matcher import Matcher
        m = Matcher(store, args.embedder, cache_dir=args.cache_dir)
        m.load_model(args.model_id)
        print(json.dumps(m.infer(scope=args.scope, limit=args.limit, min_prob=args.min_prob))); return 0
    if args.cmd == "resolve":
        from .resolve import resolve
        print(json.dumps(resolve(store, args.threshold), indent=1)); return 0
    if args.cmd == "query":
        from .resolve import equivalents
        n = parse_ref(store, args.node)
        if not n:
            print("node not found", file=sys.stderr); return 2
        print(store.node_label(n["node_id"]))
        for e in equivalents(store, n["node_id"], args.as_of, args.system_at, args.min_confidence):
            print(f"  {e['confidence']:.3f} {e['relation']:10s} {e['basis']:22s} [{e['effective_from'] or '..'} → {e['effective_to'] or '..'}]  {e['to']}")
        return 0
    if args.cmd == "explain":
        from .resolve import explain
        a, b = parse_ref(store, args.from_node), parse_ref(store, args.to_node)
        if not a or not b:
            print("node not found", file=sys.stderr); return 2
        print(json.dumps(explain(store, a["node_id"], b["node_id"]), indent=1, default=str)); return 0
    if args.cmd == "correct":
        a, b = parse_ref(store, args.from_node), parse_ref(store, args.to_node)
        if not a or not b:
            print("node not found", file=sys.stderr); return 2
        cid = store.add_correction(args.author, "course", args.action, args.note, from_node_id=a["node_id"], to_node_id=b["node_id"],
                                   relation=args.relation, confidence=args.confidence, effective_from=args.effective_from, effective_to=args.effective_to)
        print("correction", cid, "recorded; run `canon resolve` to apply"); return 0
    if args.cmd.startswith("people-"):
        from .people import cli as pcli
        return pcli.run(store, args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
