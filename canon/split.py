"""Deterministic train/holdout assignment of ground-truth pairs.

The unit is the directed course->course (or course->hub) positive pair. A pair's split is a pure
function of (pair_key, salt), so it is stable across retrains and across machines. Negatives (pairs
inside a coverage universe with no positive assertion) use the same function, so precision on the
holdout side is measured on pairs the model never trained on.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, Iterable, List, Set, Tuple

from .models import MEMBER_OF, SATISFIES
from .store import Store
from .text import hash_bucket

log = logging.getLogger(__name__)


def pair_key(from_node_id: int, to_node_id: int) -> str:
    return f"{from_node_id}->{to_node_id}"


def split_of(from_node_id: int, to_node_id: int, salt: str, holdout_pct: int = 20) -> str:
    return "holdout" if hash_bucket(pair_key(from_node_id, to_node_id), salt) < holdout_pct else "train"


def positive_pairs(store: Store) -> Dict[Tuple[int, int], Set[str]]:
    """All directed positive pairs implied by ground truth, with the sources that support them.
    Direct: satisfies (a->b). Hub: a->hub (member_of) and, through the hub, a->b and b->a."""
    pairs: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
    for r in store.query("SELECT from_node_id f, to_node_id t, source_code s FROM assertion WHERE relation = ?", (SATISFIES,)):
        pairs[(r["f"], r["t"])].add(r["s"])
    members: Dict[int, List[Tuple[int, str]]] = defaultdict(list)
    for r in store.query("SELECT from_node_id f, to_node_id t, source_code s FROM assertion WHERE relation = ?", (MEMBER_OF,)):
        pairs[(r["f"], r["t"])].add(r["s"])
        members[r["t"]].append((r["f"], r["s"]))
    for hub, mem in members.items():
        seen = {}
        for node, src in mem:
            seen.setdefault(node, src)
        nodes = list(seen)
        for a in nodes:
            for b in nodes:
                if a != b:
                    pairs[(a, b)].add(seen[a])
    return pairs


def assign(store: Store, salt: str = "v1", holdout_pct: int = 20) -> Dict[str, int]:
    pairs = positive_pairs(store)
    rows = []
    counts = {"train": 0, "holdout": 0}
    for (f, t) in pairs:
        s = split_of(f, t, salt, holdout_pct)
        counts[s] += 1
        rows.append((pair_key(f, t), s, salt))
    store.execute("DELETE FROM eval_split WHERE salt = ?", (salt,))
    if store.dialect == "sqlite":
        store.executemany("INSERT OR REPLACE INTO eval_split (pair_key, split, salt) VALUES (?,?,?)", rows)
    else:
        store.executemany("INSERT INTO eval_split (pair_key, split, salt) VALUES (?,?,?) ON CONFLICT (pair_key) DO UPDATE SET split = EXCLUDED.split, salt = EXCLUDED.salt", rows)
    store.commit()
    log.info("split %s: %d train, %d holdout positive pairs", salt, counts["train"], counts["holdout"])
    return counts
