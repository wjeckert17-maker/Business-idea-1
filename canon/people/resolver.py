"""Instructor identity resolution: conservative, constraint-aware agglomerative clustering.

Evidence is scored additively and transparently; the decision rule is:
  merge(a, b) iff score >= threshold
             and no hard conflict between any members of the two clusters
             and no cannot_link correction between them
             and the weaker-named side is not ambiguous (no other cluster within `margin` of the best)
Hard conflicts: different full given names, different middle initials, different emails at the same
institution, different SIS ids in the same source. A name change is only accepted with identifier
continuity (same email or same external id), never on department/course overlap alone.
Cross-institution links require an identifier (email / external id) or a human must_link.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..store import Store, now_iso
from ..text import stable_hash
from .names import ParsedName, compatibility, email_key, parse_name

log = logging.getLogger(__name__)

WEIGHTS = {"exact": 0.50, "strong": 0.35, "weak": 0.15, "email": 1.0, "external_id": 1.0, "department": 0.20,
           "course": 0.25, "courses_3plus": 0.10, "term": 0.10, "cross_institution_penalty": -0.60}
POLICY_VERSION = "1.0"


@dataclass
class M:
    mention_id: int
    source: str
    institution_id: Optional[int]
    raw_name: str
    name: ParsedName
    email: Optional[str]
    ext: Optional[str]              # 'source:external_id'
    dept: Optional[str]
    courses: Set[str]
    terms: Set[str]


def load(store: Store) -> List[M]:
    out = []
    for r in store.query("SELECT mention_id, source_code, institution_id, raw_name, email, external_id, department, courses, terms FROM mention"):
        out.append(M(r["mention_id"], r["source_code"], r["institution_id"], r["raw_name"], parse_name(r["raw_name"]),
                     email_key(r["email"]), f"{r['source_code']}:{r['external_id']}" if r["external_id"] else None,
                     (r["department"] or "").upper() or None, set(r["courses"] or []), set(r["terms"] or [])))
    return out


def pair_score(a: M, b: M) -> Tuple[float, Dict[str, object], Optional[str]]:
    """(score, reasons, hard_conflict_reason)."""
    reasons: Dict[str, object] = {}
    score = 0.0
    id_continuity = (a.email and a.email == b.email) or (a.ext and a.ext == b.ext)
    if a.ext and b.ext and a.source == b.source and a.ext != b.ext:
        return 0.0, {"conflict": "different identifiers in the same source"}, "different identifiers in the same source"
    if a.email and b.email and a.email != b.email and a.institution_id == b.institution_id and not (a.ext and a.ext == b.ext):
        return 0.0, {"conflict": f"different emails ({a.email} vs {b.email})"}, "different emails"
    level, why = compatibility(a.name, b.name)
    reasons["name"] = f"{level}: {why}"
    if level == "conflict":
        if id_continuity:
            reasons["name_change"] = "accepted: identifier continuity"
            level = "strong"
        else:
            return 0.0, reasons, why
    score += WEIGHTS[level]
    if a.email and a.email == b.email:
        score += WEIGHTS["email"]; reasons["email"] = a.email
    if a.ext and a.ext == b.ext:
        score += WEIGHTS["external_id"]; reasons["external_id"] = a.ext
    if a.dept and b.dept and a.dept == b.dept:
        score += WEIGHTS["department"]; reasons["department"] = a.dept
    shared = a.courses & b.courses
    if shared:
        score += WEIGHTS["course"]; reasons["courses"] = sorted(shared)[:5]
        if len(shared) >= 3:
            score += WEIGHTS["courses_3plus"]
    if a.terms & b.terms:
        score += WEIGHTS["term"]; reasons["terms"] = sorted(a.terms & b.terms)[:4]
    if a.institution_id != b.institution_id and not id_continuity:
        score += WEIGHTS["cross_institution_penalty"]; reasons["cross_institution"] = "no shared identifier"
    return round(score, 3), reasons, None


class UF:
    """Union-find that also keeps explicit member lists per root, so cluster-level checks are O(|A|·|B|)."""

    def __init__(self, ids):
        self.p = {i: i for i in ids}
        self.members = {i: [i] for i in ids}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            keep, drop = min(ra, rb), max(ra, rb)
            self.p[drop] = keep
            self.members[keep].extend(self.members.pop(drop))


def resolve(store: Store, threshold: float = 0.6, margin: float = 0.15) -> Dict:
    ms = load(store)
    by_id = {m.mention_id: m for m in ms}
    uf = UF(by_id)
    explanations: Dict[int, List[dict]] = defaultdict(list)
    stats = defaultdict(int)

    # 1. identifiers: same external id (same source) or same email => same person
    for key, group in _group(ms, lambda m: m.ext).items():
        for m in group[1:]:
            uf.union(group[0].mention_id, m.mention_id)
            explanations[m.mention_id].append({"linked_to": group[0].mention_id, "score": None, "reasons": {"external_id": key}})
    for key, group in _group(ms, lambda m: m.email).items():
        for m in group[1:]:
            uf.union(group[0].mention_id, m.mention_id)
            explanations[m.mention_id].append({"linked_to": group[0].mention_id, "score": None, "reasons": {"email": key}})

    # 2. human constraints
    must, cannot = [], set()
    for c in store.query("SELECT correction_id, action, mention_ids, author, note FROM correction WHERE domain = 'person'"):
        ids = [i for i in (c["mention_ids"] or []) if i in by_id]
        if c["action"] == "must_link" and len(ids) >= 2:
            for i in ids[1:]:
                uf.union(ids[0], i)
                explanations[i].append({"linked_to": ids[0], "score": None, "reasons": {"correction": c["correction_id"], "author": c["author"]}})
        elif c["action"] == "cannot_link":
            for i in ids:
                for j in ids:
                    if i < j:
                        cannot.add((i, j))

    def clusters_conflict(ra, rb) -> Optional[str]:
        A = [by_id[i] for i in uf.members[ra]]
        B = [by_id[i] for i in uf.members[rb]]
        for x in A:
            for y in B:
                if (min(x.mention_id, y.mention_id), max(x.mention_id, y.mention_id)) in cannot:
                    return "cannot_link correction"
                _, _, hard = pair_score(x, y)
                if hard:
                    return f"{x.raw_name!r} vs {y.raw_name!r}: {hard}"
        return None

    # 3. scored candidates inside blocks (institution + family-name token)
    blocks: Dict[Tuple[Optional[int], str], List[M]] = defaultdict(list)
    for m in ms:
        for tok in (m.name.family_tokens or {m.name.family_key}):
            blocks[(m.institution_id, tok)].append(m)
    scored: List[Tuple[float, int, int, Dict]] = []
    seen = set()
    for group in blocks.values():
        if len(group) > 400:
            stats["blocks_skipped_too_large"] += 1
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                key = (min(a.mention_id, b.mention_id), max(a.mention_id, b.mention_id))
                if key in seen:
                    continue
                seen.add(key)
                s, reasons, hard = pair_score(a, b)
                if hard or s < threshold:
                    continue
                scored.append((s, a.mention_id, b.mention_id, reasons))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    # best alternative per mention, for the ambiguity rule
    best: Dict[int, List[Tuple[float, int]]] = defaultdict(list)
    for s, a, b, _ in scored:
        best[a].append((s, b)); best[b].append((s, a))

    def ambiguous(x: int, y: int, s: float) -> Optional[str]:
        """x is joining y's cluster with score s. Ambiguous iff some other candidate z, within `margin`
        of s, belongs to a cluster that *conflicts* with y's cluster, i.e. z is plausibly a different
        person who fits x just as well. Alternatives compatible with y's cluster are not competitors:
        they would simply join the same person."""
        ry = uf.find(y)
        for s2, z in best[x]:
            rz = uf.find(z)
            if rz == ry or s2 < s - margin:
                continue
            if clusters_conflict(rz, ry):
                return f"also matches mention {z} ({by_id[z].raw_name!r}) at {s2:.2f}, a different person"
        return None

    def anchored(root: int) -> bool:
        return any(by_id[i].name.quality >= 1.0 for i in uf.members[root])

    for s, a, b, reasons in scored:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            continue
        conflict = clusters_conflict(ra, rb)
        if conflict:
            stats["merges_blocked_by_conflict"] += 1
            explanations[a].append({"not_linked_to": b, "score": s, "reasons": reasons, "blocked": conflict})
            continue
        if not anchored(ra) and not anchored(rb):
            # Two partial names ('LU, J' and 'Dr. J. Lu') never merge on circumstantial evidence alone:
            # they may be two people. Only identifiers (step 1) or a human must_link can join them.
            stats["merges_blocked_no_anchor"] += 1
            explanations[a].append({"not_linked_to": b, "score": s, "reasons": reasons, "blocked": "neither side has a full given name"})
            continue
        weak_side = a if not anchored(ra) else (b if not anchored(rb) else (a if by_id[a].name.quality <= by_id[b].name.quality else b))
        strong_side = b if weak_side == a else a
        amb = ambiguous(weak_side, strong_side, s)
        if amb:
            stats["merges_blocked_by_ambiguity"] += 1
            explanations[weak_side].append({"not_linked_to": strong_side, "score": s, "reasons": reasons, "ambiguous": amb})
            continue
        uf.union(a, b)
        explanations[a].append({"linked_to": b, "score": s, "reasons": reasons})
        explanations[b].append({"linked_to": a, "score": s, "reasons": reasons})
        stats["merges"] += 1

    # 4. persist bitemporally
    clusters: Dict[int, List[M]] = {root: [by_id[i] for i in ids] for root, ids in uf.members.items()}
    run_id = store.insert("resolution_run", {"started_at": now_iso(), "policy_version": "people-" + POLICY_VERSION})
    now = now_iso()
    current = {r["person_key"]: r for r in store.query("SELECT person_id, person_key FROM person WHERE system_to IS NULL")}
    current_members = defaultdict(set)
    for r in store.query("SELECT person_id, mention_id FROM person_mention WHERE system_to IS NULL"):
        current_members[r["person_id"]].add(r["mention_id"])
    new_keys = set()
    for root, members in clusters.items():
        key = stable_hash(*sorted(str(m.mention_id) for m in members))
        new_keys.add(key)
        display = max(members, key=lambda m: (m.name.quality, m.source == "sis", len(m.raw_name))).raw_name
        if key in current:
            continue
        pid = store.insert("person", {"resolution_run_id": run_id, "display_name": display, "person_key": key, "system_from": now,
                                      "profile": {"mentions": len(members), "sources": sorted({m.source for m in members}),
                                                  "institutions": sorted({m.institution_id for m in members if m.institution_id}),
                                                  "departments": sorted({m.dept for m in members if m.dept})}})
        for m in members:
            links = [e.get("score") or 1.0 for e in explanations[m.mention_id] if "linked_to" in e]
            conf = 1.0 if (len(members) == 1 or not links) else min(1.0, max(links))
            store.insert("person_mention", {"person_id": pid, "mention_id": m.mention_id, "confidence": round(conf, 3),
                                            "explanation": explanations[m.mention_id][-6:], "system_from": now})
        stats["persons_opened"] += 1
    for key, r in current.items():
        if key not in new_keys:
            store.execute("UPDATE person SET system_to = ? WHERE person_id = ?", (now, r["person_id"]))
            store.execute("UPDATE person_mention SET system_to = ? WHERE person_id = ? AND system_to IS NULL", (now, r["person_id"]))
            stats["persons_closed"] += 1
    out = dict(stats)
    out.update({"mentions": len(ms), "persons": len(clusters), "threshold": threshold, "margin": margin})
    store.execute("UPDATE resolution_run SET finished_at = ?, stats = ? WHERE run_id = ?", (now_iso(), store._j(out), run_id))
    store.commit()
    return out


def _group(ms: List[M], key) -> Dict[str, List[M]]:
    g: Dict[str, List[M]] = defaultdict(list)
    for m in ms:
        k = key(m)
        if k:
            g[k].append(m)
    return {k: v for k, v in g.items() if len(v) > 1}


def explain_mention(store: Store, mention_id: int) -> dict:
    m = store.query("SELECT * FROM mention WHERE mention_id = ?", (mention_id,))
    if not m:
        return {"error": "no such mention"}
    pm = store.query("SELECT * FROM person_mention WHERE mention_id = ? ORDER BY system_from", (mention_id,))
    people = {}
    for r in pm:
        p = store.query("SELECT * FROM person WHERE person_id = ?", (r["person_id"],))[0]
        p["members"] = store.query("""SELECT mn.mention_id, mn.source_code, mn.raw_name, mn.department, mn.courses, mn.terms, x.confidence
                                      FROM person_mention x JOIN mention mn USING (mention_id) WHERE x.person_id = ? AND x.system_to IS NULL""", (r["person_id"],))
        people[r["person_id"]] = p
    return {"mention": m[0], "parsed": vars(parse_name(m[0]["raw_name"])), "assignments": pm, "persons": people}
