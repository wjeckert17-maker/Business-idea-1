"""Candidate generation, pairwise features, a transparent linear scorer, and inference.

Design rules
  * The scorer only ever sees pairs whose split is 'train'. Holdout pairs are never used for weights
    or for the threshold.
  * Negatives come only from coverage universes (see schema.coverage) or explicit denials. A pair the
    ground truth is silent about is 'unknown' and is dropped from training and from precision.
  * Every score is decomposable: evidence stores each feature and its weighted contribution, so
    `explain` can show a registrar exactly why a pair scored what it did.
"""
from __future__ import annotations

import json
import logging
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from .embed import EmbeddingCache, HashingEmbedder, get_embedder
from .models import DENIED, MEMBER_OF, NOT_ARTICULATED, SATISFIES, SATISFIES_JOINTLY
from .split import pair_key, positive_pairs, split_of
from .store import Store, now_iso
from .text import norm_title, number_digits, sequence_marker, stable_hash

log = logging.getLogger(__name__)

FEATURES = ["emb_cos", "tfidf_cos", "jaccard", "title_exact", "seq_conflict", "seq_agree", "num_equal", "num_last3",
            "num_first_equal", "num_len_equal", "num_logdiff", "subject_equal", "subject_affinity", "units_equal",
            "units_known", "to_is_hub", "from_cc_to_univ", "same_state", "title_missing", "same_system"]
CODE_FEATURES = {"num_equal", "num_last3", "num_first_equal", "num_len_equal", "num_logdiff", "subject_equal", "subject_affinity", "same_system"}
SYSTEM_STATE = {"scns": "FL", "tccns": "TX", "cid": "CA"}
FAMILIES = ("course", "hub")   # target node type; each family gets its own scorer and threshold


@dataclass
class NodeInfo:
    node_id: int
    node_type: str
    institution_id: Optional[int]
    system_code: Optional[str]
    subject: str
    number_key: str
    title: Optional[str]
    text: str
    tokens: Set[str]
    seq: Optional[str]
    digits: Optional[str]
    units_min: Optional[float]
    units_max: Optional[float]
    inst_kind: Optional[str]
    state: Optional[str]
    eff_from: Optional[str]
    eff_to: Optional[str]
    sources: Set[str] = field(default_factory=set)
    systems: Set[str] = field(default_factory=set)   # common-numbering systems this node belongs to


def load_nodes(store: Store) -> Dict[int, NodeInfo]:
    inst = {r["institution_id"]: r for r in store.query("SELECT institution_id, kind, state FROM institution")}
    latest: Dict[int, dict] = {}
    srcs: Dict[int, Set[str]] = defaultdict(set)
    for v in store.query("SELECT node_id, source_code, title, description, units_min, units_max, effective_from, effective_to FROM node_version ORDER BY version_id"):
        srcs[v["node_id"]].add(v["source_code"])
        cur = latest.get(v["node_id"])
        if cur is None or (v["title"] and not cur.get("title")):
            latest[v["node_id"]] = v
        elif v["title"]:
            latest[v["node_id"]] = v
    out: Dict[int, NodeInfo] = {}
    for n in store.query("SELECT node_id, node_type, institution_id, system_code, subject, number_key FROM node"):
        v = latest.get(n["node_id"], {})
        title = (v.get("title") or "").strip() or None
        desc = (v.get("description") or "").strip()
        text = norm_title(title or "") + ((" " + norm_title(desc[:400])) if desc else "")
        i = inst.get(n["institution_id"], {})
        out[n["node_id"]] = NodeInfo(
            node_id=n["node_id"], node_type=n["node_type"], institution_id=n["institution_id"], system_code=n["system_code"],
            subject=n["subject"], number_key=n["number_key"], title=title, text=text, tokens=set(text.split()),
            seq=sequence_marker(title or "", n["number_key"]), digits=number_digits(n["number_key"]), units_min=v.get("units_min"), units_max=v.get("units_max"),
            inst_kind=i.get("kind"), state=i.get("state") or SYSTEM_STATE.get(n["system_code"] or ""),
            eff_from=v.get("effective_from"), eff_to=v.get("effective_to"),
            sources=srcs.get(n["node_id"], set()),
            systems={n["system_code"]} if n["system_code"] else set(),
        )
    return out


class Universe:
    """Knows which pairs have definitive labels."""

    def __init__(self, store: Store, nodes: Dict[int, NodeInfo]):
        self.nodes = nodes
        # The matcher's targets are what a source asserts directly: course->course articulation and
        # course->hub membership. Course->course pairs implied by a shared hub are 'derived': the
        # resolver produces them through the hub, so the matcher is neither trained nor scored on them.
        self.positives: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
        self.hubs_of: Dict[int, Set[int]] = defaultdict(set)
        for r in store.query("SELECT from_node_id f, to_node_id t, relation rel, source_code s FROM assertion WHERE relation IN (?, ?) AND source_code <> 'matcher'",
                             (SATISFIES, MEMBER_OF)):
            self.positives[(r["f"], r["t"])].add(r["s"])
            if r["rel"] == MEMBER_OF:
                self.hubs_of[r["f"]].add(r["t"])
        self.jointly: Set[Tuple[int, int]] = set()
        self.denied: Set[Tuple[int, int]] = set()
        for r in store.query("SELECT from_node_id f, to_node_id t, relation rel FROM assertion WHERE relation IN (?, ?, ?)",
                             (SATISFIES_JOINTLY, DENIED, NOT_ARTICULATED)):
            if r["rel"] == SATISFIES_JOINTLY:
                self.jointly.add((r["f"], r["t"]))
            else:
                self.denied.add((r["f"], r["t"]))
        self.cov: Dict[int, Dict[int, Set[str]]] = defaultdict(lambda: defaultdict(set))
        for r in store.query("SELECT from_institution_id f, to_institution_id t, source_code s FROM coverage"):
            self.cov[r["f"]][r["t"]].add(r["s"])
        self.exhaustive_systems = {"scns", "tccns"}
        self.inst_systems: Dict[int, Set[str]] = defaultdict(set)
        for r in store.query("""SELECT DISTINCT n.institution_id i, h.system_code s FROM assertion a
                                JOIN node n ON n.node_id = a.from_node_id JOIN node h ON h.node_id = a.to_node_id
                                WHERE a.relation = ? AND h.node_type = 'hub'""", (MEMBER_OF,)):
            self.inst_systems[r["i"]].add(r["s"])

    def label(self, a: int, b: int) -> Tuple[str, Set[str]]:
        """('pos'|'neg'|'unknown', sources). Sources = who says so."""
        p = self.positives.get((a, b))
        if p:
            return "pos", set(p)
        if (a, b) in self.denied:
            return "neg", {"assist"}
        if (a, b) in self.jointly:
            return "unknown", set()
        if self.hubs_of.get(a) and self.hubs_of.get(b) and self.hubs_of[a] & self.hubs_of[b]:
            return "derived", set()
        na, nb = self.nodes[a], self.nodes[b]
        if nb.node_type == "hub":
            if nb.system_code in self.exhaustive_systems and nb.system_code in self.inst_systems.get(na.institution_id, ()):
                return "neg", {nb.system_code}
            return "unknown", set()
        if na.node_type == "hub":
            return "unknown", set()
        srcs = self.cov.get(na.institution_id, {}).get(nb.institution_id)
        if srcs:
            return "neg", set(srcs)
        return "unknown", set()


class SubjectAffinity:
    """log-odds that subject codes X and Y co-occur in positive pairs, learned on TRAIN pairs only."""

    def __init__(self):
        self.table: Dict[Tuple[str, str], float] = {}

    def fit(self, pairs: Iterable[Tuple[str, str]]) -> "SubjectAffinity":
        joint, left, right, n = Counter(), Counter(), Counter(), 0
        for a, b in pairs:
            joint[(a, b)] += 1; left[a] += 1; right[b] += 1; n += 1
        for (a, b), c in joint.items():
            expected = left[a] * right[b] / max(n, 1)
            self.table[(a, b)] = math.log((c + 0.5) / (expected + 0.5))
        return self

    def get(self, a: str, b: str) -> float:
        if a == b:
            return max(self.table.get((a, b), 0.0), 2.0)
        return max(-3.0, min(6.0, self.table.get((a, b), 0.0)))


def features(a: NodeInfo, b: NodeInfo, emb_cos: float, tfidf_cos: float, aff: SubjectAffinity) -> List[float]:
    inter = len(a.tokens & b.tokens)
    union = len(a.tokens | b.tokens) or 1
    same_system = bool(a.systems & b.systems)
    # Course numbers only carry meaning inside one numbering system (SCNS, TCCNS, C-ID). Across
    # systems they are noise, so the number features are zeroed and the model sees `same_system`.
    da, db = (a.digits, b.digits) if same_system else (None, None)
    seq_conf = 1.0 if (a.seq and b.seq and a.seq != b.seq) else 0.0
    seq_agree = 1.0 if (a.seq and b.seq and a.seq == b.seq) else 0.0
    units_known = 1.0 if (a.units_min is not None and b.units_min is not None) else 0.0
    units_equal = 1.0 if (units_known and abs((a.units_min or 0) - (b.units_min or 0)) < 0.01) else 0.0
    return [
        emb_cos, tfidf_cos, inter / union, 1.0 if (a.text and a.text == b.text) else 0.0, seq_conf, seq_agree,
        1.0 if (da and da == db) else 0.0,
        1.0 if (da and db and da[-3:] == db[-3:]) else 0.0,
        1.0 if (da and db and da[0] == db[0]) else 0.0,
        1.0 if (da and db and len(da) == len(db)) else 0.0,
        math.log1p(abs(int(da) - int(db))) if (da and db) else 0.0,
        1.0 if a.subject == b.subject else 0.0,
        aff.get(a.subject, b.subject),
        units_equal, units_known,
        1.0 if b.node_type == "hub" else 0.0,
        1.0 if (a.inst_kind == "community_college" and b.inst_kind == "university") else 0.0,
        1.0 if (a.state and a.state == b.state) else 0.0,
        1.0 if (not a.title or not b.title) else 0.0,
        1.0 if same_system else 0.0,
    ]


class Candidates:
    """Top-k by embedding cosine, plus everything sharing (subject, digits) or (digits, last3+subject family)."""

    def __init__(self, nodes: Dict[int, NodeInfo], target_ids: List[int], E: np.ndarray, k: int = 30, k_hub: int = 10,
                 allowed_institutions: Optional[Dict[int, Set[int]]] = None):
        self.nodes, self.target_ids, self.E, self.k, self.k_hub = nodes, target_ids, E, k, k_hub
        self.pos = {nid: i for i, nid in enumerate(target_ids)}
        self.is_hub = np.array([nodes[n].node_type == "hub" for n in target_ids])
        self.allowed = allowed_institutions or {}     # query institution -> institutions it can articulate to (coverage)
        inst_cols: Dict[int, List[int]] = defaultdict(list)
        sys_cols: Dict[str, List[int]] = defaultdict(list)
        for i, nid in enumerate(target_ids):
            n = nodes[nid]
            if n.node_type == "hub":
                sys_cols[n.system_code].append(i)
            elif n.institution_id is not None:
                inst_cols[n.institution_id].append(i)
        self.inst_cols = {k: np.array(v) for k, v in inst_cols.items()}
        self.sys_cols = {k: np.array(v) for k, v in sys_cols.items()}
        self.by_code: Dict[Tuple[str, str], List[int]] = defaultdict(list)
        self.by_last3: Dict[Tuple[str, str], List[int]] = defaultdict(list)
        for nid in target_ids:
            n = nodes[nid]
            if n.digits:
                self.by_code[(n.subject, n.digits)].append(nid)
                if len(self.by_last3[(n.subject, n.digits[-3:])]) < 300:
                    self.by_last3[(n.subject, n.digits[-3:])].append(nid)

    def _scope(self, nq: NodeInfo) -> Optional[np.ndarray]:
        """Column indices of plausible targets: courses at institutions the query's institution has
        coverage toward, plus hubs of the query's numbering systems. None = no information, use all."""
        cols = [self.sys_cols[sy] for sy in nq.systems if sy in self.sys_cols]
        for inst in self.allowed.get(nq.institution_id, ()):
            if inst in self.inst_cols:
                cols.append(self.inst_cols[inst])
        if not cols:
            return None
        return np.concatenate(cols)

    def for_queries(self, q_ids: List[int], block: int = 256) -> Dict[int, List[int]]:
        out: Dict[int, List[int]] = {}
        groups: Dict[Optional[Tuple[int, ...]], List[int]] = defaultdict(list)
        scopes: Dict[Optional[Tuple[int, ...]], Optional[np.ndarray]] = {}
        for q in q_ids:
            nq = self.nodes[q]
            key = (nq.institution_id, tuple(sorted(nq.systems)))
            if key not in scopes:
                scopes[key] = self._scope(nq)
            groups[key].append(q)
        for key, qs in groups.items():
            cols = scopes[key]
            Et = self.E if cols is None else self.E[cols]
            for s in range(0, len(qs), block):
                qb = qs[s:s + block]
                Q = np.stack([self.E[self.pos[q]] if q in self.pos else np.zeros(self.E.shape[1], dtype=np.float32) for q in qb])
                sims = Q @ Et.T
                kk = min(self.k * 4, sims.shape[1])
                top = np.argpartition(-sims, kk - 1, axis=1)[:, :kk] if kk < sims.shape[1] else np.tile(np.arange(sims.shape[1]), (len(qb), 1))
                self._collect(qb, sims, top, cols, out)
        return out

    def _collect(self, qb, sims, top, cols, out):
        if True:
            for i, q in enumerate(qb):
                nq = self.nodes[q]
                courses: Dict[int, float] = {}
                hubs: Dict[int, float] = {}
                for jj in top[i]:
                    j = int(cols[jj]) if cols is not None else int(jj)
                    t = self.target_ids[j]
                    nt = self.nodes[t]
                    if t == q or (nt.node_type == "course" and nt.institution_id == nq.institution_id):
                        continue
                    (hubs if self.is_hub[j] else courses)[t] = float(sims[i, jj])
                keep = sorted(courses, key=courses.get, reverse=True)[:self.k] + sorted(hubs, key=hubs.get, reverse=True)[:self.k_hub]
                extra = set()
                if nq.digits:
                    extra |= set(self.by_code.get((nq.subject, nq.digits), []))
                    extra |= set(self.by_last3.get((nq.subject, nq.digits[-3:]), []))
                extra.discard(q)
                allowed = self.allowed.get(nq.institution_id)
                extra = [t for t in extra if not (self.nodes[t].node_type == "course" and self.nodes[t].institution_id == nq.institution_id)
                         and (self.nodes[t].node_type == "hub" or not allowed or self.nodes[t].institution_id in allowed)]
                out[q] = list(dict.fromkeys(keep + extra))


class LinearScorer:
    def __init__(self, names: List[str], mean, std, w, b):
        self.names, self.mean, self.std, self.w, self.b = names, np.asarray(mean), np.asarray(std), np.asarray(w), float(b)

    @staticmethod
    def fit(X: np.ndarray, y: np.ndarray, names: List[str], l2: float = 1e-2, iters: int = 30, disabled: Set[str] = frozenset()) -> "LinearScorer":
        mean, std = X.mean(0), X.std(0) + 1e-6
        Z = (X - mean) / std
        mask = np.array([0.0 if n in disabled else 1.0 for n in names])
        Z = Z * mask
        Zb = np.hstack([Z, np.ones((len(Z), 1))])
        w = np.zeros(Zb.shape[1])
        reg = np.full(Zb.shape[1], l2); reg[-1] = 0.0
        for _ in range(iters):
            p = 1 / (1 + np.exp(-(Zb @ w)))
            g = Zb.T @ (p - y) + reg * w
            H = (Zb * (p * (1 - p))[:, None]).T @ Zb + np.diag(reg) + 1e-9 * np.eye(len(w))
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-7:
                break
        return LinearScorer(names, mean, std, w[:-1] * mask, w[-1])

    def score(self, X: np.ndarray) -> np.ndarray:
        Z = (X - self.mean) / self.std
        return 1 / (1 + np.exp(-(Z @ self.w + self.b)))

    def contributions(self, x: Sequence[float]) -> Dict[str, float]:
        z = (np.asarray(x) - self.mean) / self.std
        return {n: round(float(zi * wi), 4) for n, zi, wi in zip(self.names, z, self.w) if wi != 0.0}

    def to_json(self) -> Dict:
        return {"mean": self.mean.tolist(), "std": self.std.tolist(), "w": self.w.tolist(), "b": self.b}

    @staticmethod
    def from_json(names, d) -> "LinearScorer":
        return LinearScorer(names, d["mean"], d["std"], d["w"], d["b"])


@dataclass
class ScoredPair:
    a: int
    b: int
    x: List[float]
    label: str
    sources: Set[str]
    split: str
    prob: float = 0.0


class Matcher:
    def __init__(self, store: Store, embedder_name: str = "hashing", cache_dir: str = ".", salt: str = "v1",
                 holdout_pct: int = 20, seed: int = 13):
        self.store, self.salt, self.holdout_pct, self.seed = store, salt, holdout_pct, seed
        self.embedder_name = embedder_name
        self.embedder = get_embedder(embedder_name)
        self.cache = EmbeddingCache(f"{cache_dir}/emb_{self.embedder.name}.npz", self.embedder)
        self.hash = HashingEmbedder()  # for the exact tfidf feature
        self.nodes = load_nodes(store)
        self.universe = Universe(store, self.nodes)
        self.target_ids = [n for n, i in self.nodes.items() if i.text]
        log.info("nodes=%d with text=%d positives=%d", len(self.nodes), len(self.target_ids), len(self.universe.positives))
        texts = [self.nodes[n].text for n in self.target_ids]
        self.hash.fit(texts)
        self._sparse: Dict[int, Dict[int, float]] = {}
        t0 = datetime.now()
        self.E = self.cache.encode(texts)
        log.info("embedded %d texts with %s in %.0fs", len(texts), self.embedder.name, (datetime.now() - t0).total_seconds())
        for n in self.nodes.values():
            if n.node_type == "course" and n.institution_id in self.universe.inst_systems:
                n.systems = set(self.universe.inst_systems[n.institution_id])
        allowed = {i: set(t) for i, t in self.universe.cov.items()}
        self.cands = Candidates(self.nodes, self.target_ids, self.E, allowed_institutions=allowed)
        self.pos = {n: i for i, n in enumerate(self.target_ids)}
        self.aff = SubjectAffinity()
        self.scorers: Dict[str, LinearScorer] = {}
        self.thresholds: Dict[str, float] = {}
        self.threshold: Optional[float] = None      # min over families; what `resolve` uses
        self.model_id: Optional[int] = None

    def family(self, p: "ScoredPair") -> str:
        return "hub" if self.nodes[p.b].node_type == "hub" else "course"

    def score_pairs(self, pairs: List["ScoredPair"]) -> None:
        for fam in FAMILIES:
            sub = [p for p in pairs if self.family(p) == fam]
            sc = self.scorers.get(fam)
            if not sub:
                continue
            if sc is None:
                for p in sub:
                    p.prob = 0.0
                continue
            probs = sc.score(np.array([p.x for p in sub], dtype=np.float64))
            for p, pr in zip(sub, probs):
                p.prob = float(pr)

    # -- helpers ------------------------------------------------------------
    def _sp(self, nid: int) -> Dict[int, float]:
        v = self._sparse.get(nid)
        if v is None:
            v = self._sparse[nid] = self.hash.sparse(self.nodes[nid].text)
        return v

    def _emb_cos(self, a: int, b: int) -> float:
        ia, ib = self.pos.get(a), self.pos.get(b)
        if ia is None or ib is None:
            return 0.0
        return float(self.E[ia] @ self.E[ib])

    def pair(self, a: int, b: int) -> ScoredPair:
        na, nb = self.nodes[a], self.nodes[b]
        x = features(na, nb, self._emb_cos(a, b), HashingEmbedder.sparse_cos(self._sp(a), self._sp(b)), self.aff)
        label, srcs = self.universe.label(a, b)
        return ScoredPair(a, b, x, label, srcs, split_of(a, b, self.salt, self.holdout_pct))

    def query_sample(self, n: int, scope: Optional[str] = None, seed_offset: int = 0) -> List[int]:
        """Course nodes with at least one positive pair, sampled per ground-truth source so that small
        sources (ASSIST) are represented, not drowned by SCNS."""
        rng = random.Random(self.seed + seed_offset)
        with_pos = {a for (a, _) in self.universe.positives}
        pools: Dict[str, List[int]] = defaultdict(list)
        for nid in self.target_ids:
            node = self.nodes[nid]
            if node.node_type != "course" or nid not in with_pos or (scope and scope not in node.sources):
                continue
            for src in node.sources:
                pools[src].append(nid)
        out: List[int] = []
        seen = set()
        per = max(1, n // max(1, len(pools)))
        for src in sorted(pools):
            rng.shuffle(pools[src])
            for nid in pools[src][:per]:
                if nid not in seen:
                    seen.add(nid); out.append(nid)
        rng.shuffle(out)
        return out

    def scored_pairs(self, q_ids: List[int], include_missing_positives: bool = True) -> Tuple[List[ScoredPair], List[Tuple[int, int, Set[str]]]]:
        """Candidate pairs with features, plus positives that candidate generation missed (as FN records)."""
        cand = self.cands.for_queries(q_ids)
        pairs, missed = [], []
        pos_by_a: Dict[int, List[Tuple[int, Set[str]]]] = defaultdict(list)
        if include_missing_positives:
            for (a, b), s in self.universe.positives.items():
                pos_by_a[a].append((b, s))
        for i, q in enumerate(q_ids):
            got = set(cand.get(q, []))
            for t in got:
                pairs.append(self.pair(q, t))
            if (i + 1) % 5000 == 0:
                log.info("features: %d/%d queries, %d pairs", i + 1, len(q_ids), len(pairs))
            for b, s in pos_by_a.get(q, []):
                if b not in got and b in self.nodes:
                    missed.append((q, b, s))
        return pairs, missed

    # -- training ---------------------------------------------------------------
    def train(self, n_queries: int = 20000, neg_ratio: int = 5, target_precision: float = 0.98,
              disabled: Set[str] = frozenset(), l2: float = 1e-2) -> Dict:
        q = self.query_sample(n_queries)
        # subject affinity from TRAIN positives only
        self.aff = SubjectAffinity().fit(
            (self.nodes[a].subject, self.nodes[b].subject) for (a, b) in self.universe.positives
            if a in self.nodes and b in self.nodes and split_of(a, b, self.salt, self.holdout_pct) == "train")
        pairs, _ = self.scored_pairs(q, include_missing_positives=False)
        train = [p for p in pairs if p.split == "train" and p.label in ("pos", "neg")]
        # validation slice inside train (by pair hash), for threshold selection
        is_val = {(p.a, p.b) for p in train if stable_hash(pair_key(p.a, p.b), self.salt + ":val")[:2] < "40"}
        val = [p for p in train if (p.a, p.b) in is_val]
        fit = [p for p in train if (p.a, p.b) not in is_val]
        log.info("train pairs=%d (fit=%d, val=%d)", len(train), len(fit), len(val))
        rng = random.Random(self.seed)
        self.scorers, self.thresholds = {}, {}
        stats = {"queries": len(q), "train_pairs": len(train), "val_pairs": len(val), "target_precision": target_precision,
                 "disabled": sorted(disabled), "families": {}}
        for fam in FAMILIES:
            pos = [p for p in fit if p.label == "pos" and self.family(p) == fam]
            neg = [p for p in fit if p.label == "neg" and self.family(p) == fam]
            if len(pos) < 50 or len(neg) < 50:
                stats["families"][fam] = {"skipped": f"too few labeled pairs (pos={len(pos)}, neg={len(neg)})"}
                continue
            rng.shuffle(neg)
            neg = neg[:max(len(pos) * neg_ratio, 1000)]
            X = np.array([p.x for p in pos + neg], dtype=np.float64)
            y = np.array([1.0] * len(pos) + [0.0] * len(neg))
            self.scorers[fam] = LinearScorer.fit(X, y, FEATURES, l2=l2, disabled=set(disabled))
            fam_val = [p for p in val if self.family(p) == fam]
            self.score_pairs(fam_val)
            t, met = choose_threshold(fam_val, target_precision)
            self.thresholds[fam] = t
            stats["families"][fam] = {"fit_pos": len(pos), "fit_neg": len(neg), "val_pairs": len(fam_val), "threshold": t,
                                      "target_met_on_validation": met, "val": metrics_at(fam_val, t)}
        self.threshold = min(self.thresholds.values()) if self.thresholds else 0.99
        stats["threshold"] = self.threshold
        log.info("trained: %s", json.dumps({f: {k: v for k, v in d.items() if k != "val"} for f, d in stats["families"].items()}))
        return stats

    def evaluate(self, n_queries: int = 10000, seed_offset: int = 1000) -> Dict:
        """Holdout metrics. Queries are a fresh sample; only holdout-split pairs are counted."""
        q = self.query_sample(n_queries, seed_offset=seed_offset)
        pairs, missed = self.scored_pairs(q, include_missing_positives=True)
        self.score_pairs(pairs)
        hold = [p for p in pairs if p.split == "holdout"]
        missed_hold = [(a, b, s) for (a, b, s) in missed if split_of(a, b, self.salt, self.holdout_pct) == "holdout"]

        def thr(p):  # each pair is judged against its own family's threshold
            return self.thresholds.get(self.family(p), 1.01)
        def m_at(sub, missed_n, t=None):
            return metrics_at(sub, t, extra_fn=missed_n, per_pair_threshold=None if t is not None else thr)
        report = {"queries": len(q), "holdout_pairs_scored": len(hold), "holdout_positives_not_candidates": len(missed_hold),
                  "threshold": self.thresholds, "at_threshold": m_at(hold, len(missed_hold)),
                  "sweep": {str(t): m_at(hold, len(missed_hold), t) for t in (0.5, 0.8, 0.9, 0.95, 0.98, 0.99)},
                  "per_source": {}, "per_family": {}}
        for fam in FAMILIES:
            sub = [p for p in hold if self.family(p) == fam]
            miss = sum(1 for (_, b, _) in missed_hold if (self.nodes[b].node_type == "hub") == (fam == "hub"))
            report["per_family"][fam] = m_at(sub, miss)
        for src in sorted({s for p in hold for s in p.sources}):
            sub = [p for p in hold if src in p.sources]
            miss = sum(1 for (_, _, s) in missed_hold if src in s)
            report["per_source"][src] = m_at(sub, miss)
        fps = sorted([p for p in hold if p.label == "neg" and p.prob >= thr(p)], key=lambda p: -p.prob)[:25]
        fns = sorted([p for p in hold if p.label == "pos" and p.prob < thr(p)], key=lambda p: p.prob)[:15]
        report["false_positives"] = [self.describe(p) for p in fps]
        report["false_negatives"] = [self.describe(p) for p in fns]
        report["missed_by_candidate_generation"] = [
            {"from": self.store.node_label(a), "to": self.store.node_label(b), "sources": sorted(s)} for (a, b, s) in missed_hold[:10]]
        return report

    def describe(self, p: ScoredPair) -> Dict:
        sc = self.scorers.get(self.family(p))
        return {"from": self.store.node_label(p.a), "to": self.store.node_label(p.b), "prob": round(p.prob, 4),
                "label": p.label, "sources": sorted(p.sources), "contributions": sc.contributions(p.x) if sc else {}}

    # -- persistence -----------------------------------------------------------------
    def save_model(self, train_stats: Dict, eval_report: Dict) -> int:
        self.model_id = self.store.insert("matcher_model", {
            "trained_at": now_iso(), "embedder": self.embedder.name, "feature_names": FEATURES,
            "weights": {"scorers": {f: s.to_json() for f, s in self.scorers.items()}, "affinity": [[a, b, v] for (a, b), v in self.aff.table.items()]},
            "threshold": self.threshold, "metrics": {"train": train_stats, "holdout": eval_report},
            "params": {"salt": self.salt, "holdout_pct": self.holdout_pct, "seed": self.seed, "thresholds": self.thresholds}})
        self.store.commit()
        return self.model_id

    def load_model(self, model_id: Optional[int] = None) -> int:
        rows = self.store.query("SELECT * FROM matcher_model WHERE model_id = ?", (model_id,)) if model_id else \
            self.store.query("SELECT * FROM matcher_model ORDER BY model_id DESC LIMIT 1")
        if not rows:
            raise RuntimeError("no trained model")
        m = rows[0]
        self.scorers = {f: LinearScorer.from_json(m["feature_names"], d) for f, d in m["weights"]["scorers"].items()}
        self.thresholds = {k: float(v) for k, v in (m["params"].get("thresholds") or {}).items()}
        self.aff = SubjectAffinity(); self.aff.table = {(a, b): v for a, b, v in m["weights"]["affinity"]}
        self.threshold, self.model_id = m["threshold"], m["model_id"]
        return self.model_id

    # -- inference --------------------------------------------------------------------
    def infer(self, scope: Optional[str] = None, limit: Optional[int] = None, min_prob: float = 0.5, block: int = 2000) -> Dict:
        """Score candidates for course nodes (optionally only those seen by `scope` source) and write
        matcher assertions for pairs with prob >= min_prob that ground truth does not already assert."""
        assert self.scorers and self.model_id is not None
        q_all = [nid for nid in self.target_ids if self.nodes[nid].node_type == "course" and (scope is None or scope in self.nodes[nid].sources)]
        if limit:
            q_all = q_all[:limit]
        run_id = self.store.start_run("matcher", {"model_id": self.model_id, "scope": scope, "limit": limit, "min_prob": min_prob})
        written, scored = 0, 0
        for s in range(0, len(q_all), block):
            qb = q_all[s:s + block]
            pairs, _ = self.scored_pairs(qb, include_missing_positives=False)
            if not pairs:
                continue
            self.score_pairs(pairs)
            rows = []
            for p in pairs:
                pr = p.prob
                scored += 1
                fam_t = self.thresholds.get(self.family(p), 1.01)
                if pr < min_prob or pr < fam_t or p.label == "pos":
                    continue
                na, nb = self.nodes[p.a], self.nodes[p.b]
                eff_from = max([d for d in (na.eff_from, nb.eff_from) if d], default=None)
                eff_to = min([d for d in (na.eff_to, nb.eff_to) if d], default=None)
                rel = MEMBER_OF if nb.node_type == "hub" else SATISFIES
                rows.append({"from_node_id": p.a, "to_node_id": p.b, "relation": rel, "confidence": round(float(pr), 4),
                             "source_code": "matcher", "run_id": run_id, "source_ref": f"matcher:model:{self.model_id}",
                             "effective_from": eff_from, "effective_to": eff_to,
                             "evidence": {"model_id": self.model_id, "embedder": self.embedder.name, "family": self.family(p),
                                          "family_threshold": fam_t,
                                          "features": dict(zip(FEATURES, [round(v, 4) for v in p.x])),
                                          "contributions": self.scorers[self.family(p)].contributions(p.x), "label_at_inference": p.label},
                             "dedup_key": stable_hash(str(p.a), str(p.b), rel, "matcher", f"model:{self.model_id}", eff_from or "", "")})
            written += self.store.add_assertions_bulk(rows)
            self.store.commit()
            log.info("infer: %d/%d queries, %d scored, %d written", min(s + block, len(q_all)), len(q_all), scored, written)
        stats = {"queries": len(q_all), "scored": scored, "written": written, "min_prob": min_prob}
        self.store.finish_run(run_id, stats)
        return stats


def metrics_at(pairs: List[ScoredPair], t: Optional[float], extra_fn: int = 0, per_pair_threshold=None) -> Dict:
    th = per_pair_threshold if per_pair_threshold is not None else (lambda p: t)
    tp = sum(1 for p in pairs if p.label == "pos" and p.prob >= th(p))
    fp = sum(1 for p in pairs if p.label == "neg" and p.prob >= th(p))
    fn = sum(1 for p in pairs if p.label == "pos" and p.prob < th(p)) + extra_fn
    unk = sum(1 for p in pairs if p.label == "unknown" and p.prob >= th(p))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "predicted_unverifiable": unk, "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0}


def choose_threshold(val: List[ScoredPair], target_precision: float, min_recall: float = 0.02) -> Tuple[float, bool]:
    """(threshold, target_met). The lowest threshold whose cumulative validation precision meets the
    target while covering at least `min_recall` of validation positives. If no such point exists the
    target is reported as unmet and the most precise threshold with recall >= min_recall is returned,
    so a model that cannot reach the bar is visible in the report rather than silently useless."""
    labeled = sorted([p for p in val if p.label in ("pos", "neg")], key=lambda p: -p.prob)
    if not labeled:
        return 0.9, False
    tp = fp = 0
    total_pos = sum(1 for p in labeled if p.label == "pos") or 1
    candidates = []
    for p in labeled:
        if p.label == "pos":
            tp += 1
        else:
            fp += 1
        candidates.append((p.prob, tp / (tp + fp), tp / total_pos))
    usable = [c for c in candidates if c[2] >= min_recall]
    ok = [c for c in usable if c[1] >= target_precision]
    if ok:
        return round(min(max(min(ok, key=lambda c: c[0])[0], 0.5), 0.995), 4), True
    if usable:
        return round(min(max(max(usable, key=lambda c: (c[1], c[2]))[0], 0.5), 0.995), 4), False
    return 0.9, False
