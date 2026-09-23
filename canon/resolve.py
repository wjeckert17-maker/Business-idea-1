"""Resolution policy: turn assertions + corrections into the believed graph, bitemporally.

Precedence for a directed pair (from, to):
  1. corrections (domain=course): reject -> no edge (vetoed); assert -> edge, basis=correction
  2. ground-truth positives (articulation / common numbering): edge, basis=ground_truth,
     confidence = max(assertion.confidence * source.base_trust)
  3. matcher: edge only if confidence >= threshold AND no ground-truth negative (denied/not_articulated)
     for the same pair; basis=inferred
Each distinct effective interval among the supporting assertions yields its own edge, so
"what did this map to under the 2023 catalog" is a filter, not a recomputation.

System time: every run computes the full current edge set, closes edges that disappeared
(system_to = now) and opens edges that are new. Nothing is deleted.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .models import DENIED, MEMBER_OF, NEGATIVE_RELATIONS, NOT_ARTICULATED, POSITIVE_RELATIONS, SATISFIES, SATISFIES_JOINTLY
from .store import Store, now_iso
from .text import stable_hash

log = logging.getLogger(__name__)

POLICY_VERSION = "1.0"
HUB_TRANSITIVITY = {"scns": 0.98, "tccns": 0.98, "cid": 0.95}


def _interval_contains(eff_from: Optional[str], eff_to: Optional[str], d: Optional[str]) -> bool:
    if d is None:
        return True
    return (eff_from is None or eff_from <= d) and (eff_to is None or eff_to > d)


def resolve(store: Store, matcher_threshold: Optional[float] = None) -> Dict:
    started = now_iso()
    run_id = store.insert("resolution_run", {"started_at": started, "policy_version": POLICY_VERSION})
    trust = {r["source_code"]: r["base_trust"] for r in store.query("SELECT source_code, base_trust FROM source")}
    if matcher_threshold is None:
        m = store.query("SELECT threshold FROM matcher_model ORDER BY model_id DESC LIMIT 1")
        matcher_threshold = m[0]["threshold"] if m else 0.99

    by_pair: Dict[Tuple[int, int], List[dict]] = defaultdict(list)
    for a in store.query("SELECT assertion_id, from_node_id, to_node_id, relation, confidence, source_code, effective_from, effective_to, group_key FROM assertion"):
        by_pair[(a["from_node_id"], a["to_node_id"])].append(a)
    corr: Dict[Tuple[int, int], List[dict]] = defaultdict(list)
    for c in store.query("SELECT * FROM correction WHERE domain = 'course' AND from_node_id IS NOT NULL AND to_node_id IS NOT NULL"):
        corr[(c["from_node_id"], c["to_node_id"])].append(c)

    new_edges: Dict[str, dict] = {}
    stats = defaultdict(int)

    def add(f, t, rel, conf, basis, eff_from, eff_to, support, vetoes=None):
        key = stable_hash(str(f), str(t), rel, basis, eff_from or "", eff_to or "", f"{conf:.4f}")
        new_edges[key] = {"from_node_id": f, "to_node_id": t, "relation": rel, "confidence": round(conf, 4), "basis": basis,
                          "effective_from": eff_from, "effective_to": eff_to, "support": support, "vetoes": vetoes or [],
                          "edge_key": key}
        stats[basis] += 1

    for (f, t) in set(by_pair) | set(corr):
        asserts = by_pair.get((f, t), [])
        cs = corr.get((f, t), [])
        rejects = [c for c in cs if c["action"] == "reject"]
        if rejects:
            stats["vetoed_by_correction"] += 1
            continue
        handled_by_correction = False
        for c in cs:
            if c["action"] == "assert":
                add(f, t, c["relation"] or SATISFIES, c["confidence"] or 1.0, "correction", c["effective_from"], c["effective_to"],
                    [{"correction_id": c["correction_id"], "author": c["author"], "note": c["note"]}])
                handled_by_correction = True
        if handled_by_correction:
            continue
        gt_pos = [a for a in asserts if a["relation"] in POSITIVE_RELATIONS and a["source_code"] != "matcher"]
        gt_joint = [a for a in asserts if a["relation"] == SATISFIES_JOINTLY]
        gt_neg = [a for a in asserts if a["relation"] in NEGATIVE_RELATIONS]
        inferred = [a for a in asserts if a["source_code"] == "matcher" and a["relation"] in POSITIVE_RELATIONS]
        if gt_pos:
            for rel in {a["relation"] for a in gt_pos}:
                intervals = defaultdict(list)
                for a in gt_pos:
                    if a["relation"] == rel:
                        intervals[(a["effective_from"], a["effective_to"])].append(a)
                for (ef, et), group in intervals.items():
                    conf = max(a["confidence"] * trust.get(a["source_code"], 1.0) for a in group)
                    add(f, t, rel, conf, "ground_truth", ef, et,
                        [{"assertion_id": a["assertion_id"], "source": a["source_code"]} for a in group],
                        vetoes=[{"assertion_id": a["assertion_id"], "relation": a["relation"], "note": "negative evidence exists but positive ground truth wins"} for a in gt_neg])
            continue
        if gt_joint:
            for a in gt_joint:
                add(f, t, SATISFIES_JOINTLY, a["confidence"] * trust.get(a["source_code"], 1.0), "ground_truth", a["effective_from"], a["effective_to"],
                    [{"assertion_id": a["assertion_id"], "source": a["source_code"], "group_key": a["group_key"]}])
            continue
        if inferred:
            best = max(inferred, key=lambda a: a["confidence"])
            if gt_neg:
                stats["inferred_vetoed_by_negative"] += 1
                continue
            if best["confidence"] >= matcher_threshold:
                add(f, t, best["relation"], best["confidence"], "inferred", best["effective_from"], best["effective_to"],
                    [{"assertion_id": a["assertion_id"], "source": "matcher", "confidence": a["confidence"]} for a in inferred])
            else:
                stats["inferred_below_threshold"] += 1

    # bitemporal write
    current = {r["edge_key"]: r["edge_id"] for r in store.query("SELECT edge_id, edge_key FROM resolved_edge WHERE system_to IS NULL")}
    closed = [eid for k, eid in current.items() if k not in new_edges]
    opened = [e for k, e in new_edges.items() if k not in current]
    now = now_iso()
    for i in range(0, len(closed), 500):
        chunk = closed[i:i + 500]
        store.execute(f"UPDATE resolved_edge SET system_to = ? WHERE edge_id IN ({','.join('?' * len(chunk))})", [now, *chunk])
    for e in opened:
        e.update({"system_from": now, "resolution_run_id": run_id})
        store.insert("resolved_edge", e)
    out = dict(stats)
    out.update({"edges_current": len(new_edges), "opened": len(opened), "closed": len(closed), "matcher_threshold": matcher_threshold})
    store.execute("UPDATE resolution_run SET finished_at = ?, stats = ? WHERE run_id = ?", (now_iso(), store._j(out), run_id))
    store.commit()
    log.info("resolved: %s", json.dumps(out))
    return out


# -- queries -------------------------------------------------------------------
def current_edges(store: Store, node_id: int, direction: str = "out", effective_at: Optional[str] = None,
                  system_at: Optional[str] = None) -> List[dict]:
    col = "from_node_id" if direction == "out" else "to_node_id"
    sys_clause = "system_to IS NULL" if system_at is None else "system_from <= ? AND (system_to IS NULL OR system_to > ?)"
    params: List = [node_id] + ([] if system_at is None else [system_at, system_at])
    rows = store.query(f"SELECT * FROM resolved_edge WHERE {col} = ? AND {sys_clause}", params)
    return [r for r in rows if _interval_contains(r["effective_from"], r["effective_to"], effective_at)]


def equivalents(store: Store, node_id: int, effective_at: Optional[str] = None, system_at: Optional[str] = None,
                min_confidence: float = 0.0, include_hub_paths: bool = True) -> List[dict]:
    """Directed: what does `node_id` satisfy? Direct edges plus two-hop paths through hubs."""
    out = []
    for e in current_edges(store, node_id, "out", effective_at, system_at):
        if e["confidence"] >= min_confidence:
            out.append({"to_node_id": e["to_node_id"], "to": store.node_label(e["to_node_id"]), "relation": e["relation"],
                        "confidence": e["confidence"], "basis": e["basis"], "effective_from": e["effective_from"],
                        "effective_to": e["effective_to"], "path": [e["edge_id"]]})
        if include_hub_paths and e["relation"] == MEMBER_OF:
            hub = store.query("SELECT system_code FROM node WHERE node_id = ?", (e["to_node_id"],))[0]["system_code"]
            tr = HUB_TRANSITIVITY.get(hub, 0.9)
            for m in current_edges(store, e["to_node_id"], "in", effective_at, system_at):
                if m["from_node_id"] == node_id or m["relation"] != MEMBER_OF:
                    continue
                conf = round(min(e["confidence"], m["confidence"]) * tr, 4)
                if conf >= min_confidence:
                    out.append({"to_node_id": m["from_node_id"], "to": store.node_label(m["from_node_id"]), "relation": SATISFIES,
                                "confidence": conf, "basis": f"derived_via_hub:{hub}", "effective_from": max(filter(None, [e["effective_from"], m["effective_from"]]), default=None),
                                "effective_to": min(filter(None, [e["effective_to"], m["effective_to"]]), default=None), "path": [e["edge_id"], m["edge_id"]]})
    out.sort(key=lambda r: -r["confidence"])
    return out


def explain(store: Store, from_id: int, to_id: int) -> dict:
    """Everything a registrar could ask about one directed pair."""
    def hydrate(a):
        src = store.query("SELECT name, kind, base_trust FROM source WHERE source_code = ?", (a["source_code"],))
        a["source"] = src[0] if src else None
        run = store.query("SELECT started_at, code_version, params FROM import_run WHERE run_id = ?", (a["run_id"],)) if a.get("run_id") else []
        a["import_run"] = run[0] if run else None
        return a
    res = {"from": store.node_label(from_id), "to": store.node_label(to_id),
           "resolved_edges": store.query("SELECT * FROM resolved_edge WHERE from_node_id = ? AND to_node_id = ? ORDER BY system_from", (from_id, to_id)),
           "assertions": [hydrate(a) for a in store.query("SELECT * FROM assertion WHERE from_node_id = ? AND to_node_id = ? ORDER BY asserted_at", (from_id, to_id))],
           "corrections": store.query("SELECT * FROM correction WHERE domain = 'course' AND from_node_id = ? AND to_node_id = ?", (from_id, to_id)),
           "hub_paths": []}
    # derived paths: from -> hub <- to
    for e in current_edges(store, from_id, "out"):
        if e["relation"] != MEMBER_OF:
            continue
        for m in current_edges(store, e["to_node_id"], "in"):
            if m["from_node_id"] == to_id:
                res["hub_paths"].append({"hub": store.node_label(e["to_node_id"]), "from_edge": e, "to_edge": m,
                                         "transitivity": HUB_TRANSITIVITY.get(store.query("SELECT system_code FROM node WHERE node_id = ?", (e["to_node_id"],))[0]["system_code"], 0.9)})
    return res
