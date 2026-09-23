from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .adapters import get_adapter
from .config import SchoolConfig
from .http import PoliteSession
from .runner import TermRun


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="course-ingest", description="Load a term of sections into the course-planning schema.")
    p.add_argument("--config", required=True, help="school TOML, e.g. schools/gatech.toml")
    p.add_argument("--term", action="append", help="term code (repeatable). Omit with --list-terms to see codes.")
    p.add_argument("--list-terms", action="store_true")
    p.add_argument("--dsn", default=os.environ.get("COURSE_INGEST_DSN"), help="PostgreSQL DSN (or env COURSE_INGEST_DSN)")
    p.add_argument("--dry-run", action="store_true", help="fetch and parse only; no database")
    p.add_argument("--limit", type=int, help="stop after N sections (smoke test; run marked incomplete)")
    p.add_argument("--mark-missing-cancelled", action="store_true",
                   help="after a complete run, set status='cancelled' on sections absent from the feed")
    p.add_argument("--failures-dir", default=".", help="where to write failures-<school>-<term>-<ts>.jsonl")
    p.add_argument("--min-interval", type=float, default=1.0, help="seconds between requests (default 1.0)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = SchoolConfig.load(args.config)
    http = PoliteSession(cfg.base_url, cfg.user_agent, min_interval=args.min_interval)
    adapter = get_adapter(cfg.adapter)(http, cfg)

    if args.list_terms:
        for t in adapter.list_terms():
            print(f"{t.code}\t{t.name}\t{t.season} {t.year}")
        return 0
    if not args.term:
        p.error("--term is required (or --list-terms)")
    if not args.dry_run and not args.dsn:
        p.error("--dsn (or COURSE_INGEST_DSN) is required unless --dry-run")

    terms = {t.code: t for t in adapter.list_terms()}
    loader = None
    if not args.dry_run:
        import psycopg
        from .loader import Loader
        conn = psycopg.connect(args.dsn)
        loader = Loader(conn, cfg)

    exit_code = 0
    for code in args.term:
        term = terms.get(code)
        if term is None:
            print(f"term {code!r} not offered by source; known: {', '.join(sorted(terms))}", file=sys.stderr)
            exit_code = 2
            continue
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fpath = Path(args.failures_dir) / f"failures-{cfg.slug}-{code}-{ts}.jsonl"
        with open(fpath, "w") as ff:
            summary = TermRun(adapter, loader, term, failures_file=ff, limit=args.limit,
                              mark_missing_cancelled=args.mark_missing_cancelled).run()
        if summary.failed == 0:
            fpath.unlink(missing_ok=True)
        else:
            print(f"failures with raw payloads: {fpath}", file=sys.stderr)
        print(summary.to_json())
        if summary.error or not summary.complete:
            exit_code = max(exit_code, 1)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
