"""Run one ground-truth adapter into the store, with provenance."""
from __future__ import annotations

import logging
from collections import Counter
from typing import Dict

from .models import AssertionRecord, CourseRef, CoverageRecord
from .sources import get_adapter
from .store import Store

log = logging.getLogger(__name__)


def ingest(store: Store, source_code: str, path: str, **options) -> Dict[str, int]:
    cls = get_adapter(source_code)
    store.ensure_source(cls.code, cls.name, cls.kind, cls.base_trust, cls.notes)
    run_id = store.start_run(cls.code, {"path": path, **{k: str(v) for k, v in options.items()}})
    adapter = cls(path, **options)
    stats: Counter = Counter()
    for rec in adapter.records():
        if isinstance(rec, CourseRef):
            store.node_id(rec, cls.code, run_id)
            stats["hubs" if rec.is_hub else "courses"] += 1
        elif isinstance(rec, AssertionRecord):
            if store.add_assertion(rec, cls.code, run_id) is not None:
                stats["assertions_" + rec.relation] += 1
            else:
                stats["assertions_duplicate"] += 1
        elif isinstance(rec, CoverageRecord):
            store.add_coverage(rec, cls.code, run_id)
            stats["coverage"] += 1
        stats["records"] += 1
        if stats["records"] % 50000 == 0:
            store.commit()
            log.info("%s: %d records", cls.code, stats["records"])
    store.commit()
    store.finish_run(run_id, dict(stats))
    return dict(stats)
