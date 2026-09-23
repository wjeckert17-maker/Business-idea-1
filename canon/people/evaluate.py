"""Synthetic-but-grounded evaluation: real SIS instructors (with stable SIS ids as gold labels) plus
generated grade-feed, syllabus and rating-site mentions in the messy forms those sources really use.
Distractors are not injected: real departments already contain people who share a surname."""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from typing import Dict, List

from ..models import InstitutionRef, MentionRecord
from ..store import Store
from .names import parse_name
from .resolver import resolve


def synthesize(sis_rows: List[dict], seed: int = 5) -> List[MentionRecord]:
    """sis_rows: dicts with external_id, display_name, email, courses, terms, department."""
    rng = random.Random(seed)
    out = []
    for r in sis_rows:
        p = parse_name(r["display_name"])
        if not p.given:
            continue
        fam, given = p.family, p.given
        mid = p.middle[0] if p.middle else None
        gold = {"gold_id": r["external_id"]}
        courses, terms, dept = r["courses"], r["terms"], r["department"]
        # grade feed: one row per course, 'LAST, F' or 'Last, First'
        for c in courses[:3]:
            style = rng.random()
            if style < 0.45:
                name = f"{fam.upper()}, {given[0]}"
            elif style < 0.8:
                name = f"{fam}, {given}"
            else:
                name = f"{fam}, {given} {mid[0]}." if mid else f"{fam}, {given}"
            out.append(MentionRecord("grade_feed", name, department=dept, courses=[c], terms=terms[:1], extra=gold,
                                     source_ref=f"synthetic:grade:{r['external_id']}:{c}"))
        # rating site: honorific + full name, department, courses; no email
        nick = _nickname(given, rng)
        out.append(MentionRecord("rating_site", f"{rng.choice(['Prof. ', 'Dr. ', ''])}{nick} {fam}", department=dept,
                                 courses=courses[:2], extra=gold, source_ref=f"synthetic:rating:{r['external_id']}"))
        # syllabus: 'Dr. F. M. Last' with email 40% of the time
        style = rng.random()
        if style < 0.5:
            name = f"Dr. {given[0]}. {mid[0] + '. ' if mid else ''}{fam}"
        else:
            name = f"{given} {fam}, Ph.D."
        out.append(MentionRecord("syllabus", name, email=r["email"] if rng.random() < 0.4 else None, department=dept,
                                 courses=courses[:1], terms=terms[:1], extra=gold, source_ref=f"synthetic:syllabus:{r['external_id']}"))
    return out


def _nickname(given: str, rng) -> str:
    from .names import NICKNAMES
    inv = defaultdict(list)
    for k, v in NICKNAMES.items():
        inv[v].append(k)
    g = given.lower()
    if g in inv and rng.random() < 0.5:
        return rng.choice(inv[g]).capitalize()
    return given


def sis_rows_from_dump(path_or_dir: str, limit_people: int = None) -> List[dict]:
    import os
    from .mentions import SisSectionsAdapter
    inst = InstitutionRef("eval", "eval", "Evaluation Institution")
    agg: Dict[str, dict] = {}
    for m in SisSectionsAdapter(path_or_dir, inst).records():
        if not m.external_id:
            continue
        a = agg.setdefault(m.external_id, {"external_id": m.external_id, "display_name": m.raw_name, "email": m.email,
                                           "courses": [], "terms": [], "department": m.department})
        for c in m.courses:
            if c not in a["courses"]:
                a["courses"].append(c)
        for t in m.terms:
            if t not in a["terms"]:
                a["terms"].append(t)
    rows = list(agg.values())
    if limit_people:
        rows = rows[:limit_people]
    return rows


def evaluate(store: Store, sis_path: str, institution: InstitutionRef, threshold: float = 0.6, margin: float = 0.15,
             limit_people: int = None) -> Dict:
    from .mentions import SisSectionsAdapter
    run_id = store.start_run("sis", {"eval": True, "path": sis_path})
    gold: Dict[int, str] = {}
    source_of: Dict[int, str] = {}
    n = 0
    for m in SisSectionsAdapter(sis_path, institution).records():
        if not m.external_id:
            continue
        mid = store.add_mention(m, run_id, vars(parse_name(m.raw_name)))
        if mid:
            gold[mid] = m.external_id; source_of[mid] = "sis"; n += 1
        if limit_people and len(set(gold.values())) >= limit_people and n > 0:
            pass
    rows = sis_rows_from_dump(sis_path, limit_people)
    keep = {r["external_id"] for r in rows}
    if limit_people:
        # drop SIS mentions outside the limited set
        for mid, g in list(gold.items()):
            if g not in keep:
                store.execute("DELETE FROM mention WHERE mention_id = ?", (mid,)); del gold[mid]
    for m in synthesize(rows):
        m.institution = institution
        mid = store.add_mention(m, run_id, vars(parse_name(m.raw_name)))
        if mid:
            gold[mid] = m.extra["gold_id"]; source_of[mid] = m.source_code
    store.commit()
    stats = resolve(store, threshold, margin)
    assign = {r["mention_id"]: r["person_id"] for r in store.query("SELECT mention_id, person_id FROM person_mention WHERE system_to IS NULL")}
    # pairwise metrics
    by_person = defaultdict(list)
    for mid, pid in assign.items():
        if mid in gold:
            by_person[pid].append(mid)
    by_gold = defaultdict(list)
    for mid, g in gold.items():
        by_gold[g].append(mid)
    tp = fp = 0
    wrong_merges = []
    for pid, mids in by_person.items():
        for i in range(len(mids)):
            for j in range(i + 1, len(mids)):
                if gold[mids[i]] == gold[mids[j]]:
                    tp += 1
                else:
                    fp += 1
                    if len(wrong_merges) < 20:
                        wrong_merges.append((mids[i], mids[j]))
    total_pos = sum(len(v) * (len(v) - 1) // 2 for v in by_gold.values())
    fn = total_pos - tp
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / total_pos if total_pos else 1.0
    # per source: fraction of non-SIS mentions attached to the right SIS person
    sis_person_of_gold = {}
    for mid, pid in assign.items():
        if source_of.get(mid) == "sis":
            sis_person_of_gold.setdefault(gold[mid], pid)
    per_source = {}
    for src in ("grade_feed", "rating_site", "syllabus"):
        ids = [mid for mid in gold if source_of.get(mid) == src]
        right = sum(1 for mid in ids if assign.get(mid) == sis_person_of_gold.get(gold[mid]))
        wrong = sum(1 for mid in ids if assign.get(mid) != sis_person_of_gold.get(gold[mid]) and
                    any(gold[o] != gold[mid] for o in by_person.get(assign.get(mid), []) if source_of.get(o) == "sis"))
        per_source[src] = {"mentions": len(ids), "attached_to_correct_sis_person": right, "attached_to_wrong_person": wrong,
                           "left_separate": len(ids) - right - wrong, "recall": round(right / len(ids), 4) if ids else None}
    surnames = Counter((parse_name(r["display_name"]).family_key, r["department"]) for r in rows)
    shared = sum(1 for k, v in surnames.items() if v > 1)
    detail = [{"a": store.query("SELECT raw_name, source_code, department FROM mention WHERE mention_id = ?", (a,))[0],
               "b": store.query("SELECT raw_name, source_code, department FROM mention WHERE mention_id = ?", (b,))[0]} for a, b in wrong_merges[:10]]
    return {"people": len(rows), "mentions": len(gold), "surname_dept_collisions": shared, "resolver": stats,
            "pairwise": {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4)},
            "wrong_merges": fp, "wrong_merge_examples": detail, "per_source": per_source}


def render(rep: Dict) -> str:
    L = ["# Instructor identity evaluation", "",
         f"* real SIS instructors: {rep['people']}; total mentions (SIS + synthetic grade/rating/syllabus): {rep['mentions']}",
         f"* (surname, department) collisions among real instructors: {rep['surname_dept_collisions']}",
         f"* resolver: {json.dumps(rep['resolver'])}", "",
         f"## Pairwise: precision **{rep['pairwise']['precision']:.4f}**, recall {rep['pairwise']['recall']:.4f} (tp={rep['pairwise']['tp']}, fp={rep['pairwise']['fp']}, fn={rep['pairwise']['fn']})",
         f"## Wrong merges: **{rep['wrong_merges']}**", "", "| source | mentions | to correct person | to wrong person | left separate | recall |", "|---|---|---|---|---|---|"]
    for s, m in rep["per_source"].items():
        L.append(f"| {s} | {m['mentions']} | {m['attached_to_correct_sis_person']} | {m['attached_to_wrong_person']} | {m['left_separate']} | {m['recall']} |")
    if rep["wrong_merge_examples"]:
        L += ["", "### Wrong merge examples", ""]
        for e in rep["wrong_merge_examples"]:
            L.append(f"* {e['a']['raw_name']} ({e['a']['source_code']}, {e['a']['department']})  ⟷  {e['b']['raw_name']} ({e['b']['source_code']}, {e['b']['department']})")
    return "\n".join(L) + "\n"
