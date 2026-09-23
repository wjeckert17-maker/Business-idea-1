# canon: the normalization engine

Resolves courses and instructors across institutions into one directed, weighted, versioned graph.
Package `canon/`; command `python -m canon`. Storage is SQLite by default (one file, zero install)
and PostgreSQL through the same DDL (`canon/schema.py`, two dialect substitutions; both variants
parse-checked).

## 1. Ground truth first

Four human-verified sources were located, probed live on 2026-09-09, and given adapters
(`canon/sources/`). Each adapter yields nodes, directed assertions, and *coverage* facts.

| source | what it is | how reached | what an assertion means | exhaustive? |
|---|---|---|---|---|
| ASSIST (CA) | articulation agreements, CCC → CSU/UC, per academic year | JSON API behind assist.org; needs the app's own XSRF cookie echoed as a header; rate-limited (429) beyond ~100 large reports per session | sending course **satisfies** receiving course; AND-groups become `satisfies_jointly` with a shared `group_key`; `deniedCourses` become **denied** | yes, per (sending, receiving, year) agreement |
| Florida SCNS | statewide common course numbers, all 151 SCNS institutions, 193k course rows incl. discontinued | public flat-file download (fixed width, format documented by FLDOE) | course is **member_of** hub (prefix + last three digits + lab code) | yes: two SCNS courses with different numbers are by definition different |
| Texas TCCNS | common course number matrix, 141 institutions | public .xls export | local course **member_of** TCCNS hub | yes, within the matrix |
| California C-ID | CCC courses approved against C-ID descriptors, 27,650 approvals | JSON API (`/api/v1/course-list`) | course **member_of** C-ID descriptor hub | **no** — a college may simply not have submitted; absence is never a negative |

Hubs are first-class nodes. Course-to-course equivalence through a hub is *derived at query time*
(`a → hub ← b` ⇒ `a satisfies b`, confidence `min(edges) × transitivity[system]`), so the store holds
~260k assertions rather than the ~5.6M pairwise edges they imply.

Loaded in this session (see `canon_ingest_stats.json`): 313,567 nodes, 259k+ assertions, 41k coverage rows.
ASSIST volume is 279 agreements (every college articulating to SJSU and UC Davis for 2023-24, plus a slice of
2026-27); the server throttles at roughly one large report per three seconds, and the adapter reads whatever
the raw cache holds, so widening coverage is a matter of running the fetcher longer, not editing code.

## 2. Holdout before anything else

`canon split` assigns every positive pair (direct or hub-implied) to `train` or `holdout` by a salted
hash of the directed pair key: 4,509,693 train / 1,125,944 holdout. Negatives use the same hash, so
holdout precision is measured on pairs the model never saw. The salt is stored, so retrains are
comparable and a new salt is a deliberate act.

Two protocol rules matter more than the split ratio:

* **A pair is a negative only inside a coverage universe.** Silence elsewhere is `unknown`: never a
  training negative, never counted against precision. This is what stops the model from being punished
  for finding true equivalences ASSIST has not yet written down, and from being rewarded for them either.
* **Recall counts pairs the candidate generator never surfaced.** Candidate generation is part of the
  system under test.

## 3. Then, and only then, the matcher

`canon/matcher.py`. Text = title (+ description when a source has one; none of the four ground-truth
sources publish descriptions, so this session's numbers are title-only). Two embedders behind one
interface: a dependency-free hashed TF-IDF with random projection, and all-MiniLM-L6-v2. Candidates =
top-30 by cosine over all 313k nodes plus every node sharing (subject, number). Nineteen pairwise
features (embedding cosine, exact TF-IDF cosine, token Jaccard, sequence-marker agreement/conflict
such as "Calculus I" vs "II", number equality/last-three/first-digit/distance, subject equality and a
subject-affinity log-odds learned on train pairs only, units, hub flag, CC→university direction, same
state, missing title). Scorer = L2-regularised logistic regression fit by Newton's method; every score
decomposes into per-feature contributions that are stored in the assertion's evidence.

The threshold is chosen on a validation slice *of the train split* to hit a target precision (0.98 by
default); the holdout is touched once, for the report. Because SCNS and TCCNS equivalence is literally
encoded in the course number, the report also carries a **text-only ablation** with all number and
subject features disabled: that is the honest measure of how well titles alone find CS 240 ≡ COMP 2100.

### Measured result (model 7, all-MiniLM-L6-v2, titles only, all 279 fetched ASSIST agreements; full report in `matcher_eval.md`)

The matcher is scored on what sources assert directly: course→course articulation (ASSIST) and
course→hub membership (SCNS, TCCNS, C-ID). Course→course pairs implied by a shared hub are derived by
the resolver, not matched. Candidates are drawn only from institutions the query's institution has
coverage toward, plus the hubs of its numbering systems; with that scoping only 220 of ~2,400 holdout
positives are missed before scoring, so the numbers below are the scorer's.

| threshold | precision | recall |
|---|---|---|
| per-family thresholds chosen for 0.98 precision (course 0.9777, hub 0.9729) | **1.000** | **0.020** |
| 0.95 | 0.818 | 0.121 |
| 0.80 | 0.688 | 0.356 |
| 0.50 | 0.462 | 0.598 |

**The 0.98 precision target is not reachable with titles alone at any useful recall**, and the
report says so (`target NOT MET` for course targets on validation). The text-only ablation is worse
still (P=0.754 at R=0.129). The remaining false positives are sequence and level confusions that a
title cannot resolve — "General Chemistry I" against a receiving institution's "General Chemistry
Course" placeholder, a lab-suffixed SCNS number against its lecture hub. Because ground truth was loaded
first and the holdout fixed before modelling, this is a measurement, not a shipped mistake: the
resolver only admits inferred edges above the per-family thresholds, so today the inferred layer adds
very little and the graph is, in practice, the verified ground truth plus hub derivation.

What would move it, in order of expected value: course descriptions and syllabus topics for both
endpoints (the sources here publish none); a second-stage judge (cross-encoder or an LLM call) over the
top-5 candidates, where the linear scorer's job becomes recall; and per-receiving-institution
calibration, since ASSIST negatives are near-duplicates at the same university.

## 4. Directed, weighted, provenance-carrying edges

There is no merge. `assertion` is append-only: one row per (source, directed pair, relation, effective
interval) with confidence, `source_ref` (agreement key, file:line, API URL), import run, and evidence.
`resolved_edge` is what we currently believe, recomputed by `canon resolve` under a fixed policy
(`canon/resolve.py`):

1. human corrections (`assert` / `reject`) — always win
2. ground-truth positives — confidence = max(assertion confidence × source base trust)
3. matcher — only above the model threshold **and** only if no ground-truth negative exists for the pair

Every edge carries `effective_from/to` (catalog time, from the source: ASSIST academic year, SCNS
effective/discontinued dates, TCCNS matrix year, C-ID approval date) and `system_from/to` (when we
believed it). Both are query filters:

```
canon query "Evergreen Valley College | CS | 22A" --as-of 2023-10-15          # under the 2023-24 catalog
canon query "node:1234" --as-of 2024-02-01 --system-at 2026-09-01T00:00:00    # what we believed last week
canon explain "Evergreen Valley College | MATH | 071" "San Jose State University | MATH | 30"
```

`explain` returns the resolved edge history, every assertion with its source, trust, import run and
evidence, every correction, and any hub path — the full chain for a skeptical registrar.

## 5. Human corrections are replayed, not applied

`canon correct --author ... --action reject|assert FROM TO [--effective-from ...]` writes an append-only
`correction` row. Nothing is edited. `resolve` re-derives the graph from assertions + corrections each
time, so a retrained matcher can write a million new assertions and every correction still wins;
`tests/test_canon_resolver.py` proves the reject survives re-resolution and that the pre-correction
belief is still answerable with `--system-at`.

## 6. Instructor identity (`canon/people/`)

Mentions come from adapters (SIS sections, grade feed rows, syllabus extractions, rating-site exports);
each is stored verbatim with its source, institution, department, courses and terms. Resolution is a
constraint-aware agglomerative clustering with a transparent additive score (name compatibility level,
email, external id, department, shared courses, overlapping terms, a penalty for cross-institution
pairs without an identifier) and four hard rules:

* **Conflicts veto.** Different full given names, different middle initials, different emails at the
  same institution, or different SIS ids in one source are never merged, whatever else agrees. A
  name change is accepted only with identifier continuity (same email or SIS id).
* **Partial names need an anchor.** "LU, J" or "Dr. J. Lu" may join a cluster that contains a full name;
  two partial names never merge on circumstantial evidence.
* **Ambiguity means separate.** If a partial name fits two mutually conflicting anchored clusters
  within a margin, it stays its own entity and the explanation names the alternative.
* **Cross-institution links need an identifier** or a human `must_link`.

Corrections are `must_link` / `cannot_link` over mention ids, replayed on every run like course
corrections. Persons are bitemporal like edges.

Evaluation (`canon people-evaluate`) uses 2,334 real Georgia Tech instructors from the Fall and Summer
2026 SIS feeds as gold (SIS id), plus generated grade-feed ("THAYER, J", "Thayer, Jane M."), rating-site
("Prof. Bill Smith") and syllabus ("Dr. J. M. Thayer, Ph.D.") mentions in the forms those sources use.
No distractors are injected: the real departments already contain 62 (surname, department) collisions.
Result: **pairwise precision 1.0000, 0 wrong merges, recall 0.9956** over 29,546 mentions; 386 partial
names left separate as ambiguous or unanchored. Caveat: the non-SIS mentions are synthetic; real grade
files will be messier (see `people_eval.md`).

## 7. Adding an institution or a source

* A new ground-truth system: subclass `GroundTruthAdapter`, yield `CourseRef`/`AssertionRecord`/
  `CoverageRecord`, decorate with `@register`. Institution identity is resolved by the store from
  (source, external key) then normalized name; `Store.alias_institution` records human overrides.
* A new mention source: subclass `MentionAdapter`.
* Nothing in `store`, `split`, `matcher`, `resolve` or `people/resolver` changes.

## 8. What is underspecified or deliberately not done

1. **ASSIST breadth.** 279 of thousands of agreements; the fetcher is polite (3 s spacing, 90 s+ backoff on
   429) and resumable, but full coverage is hours of wall-clock per receiving institution.
2. **Descriptions and syllabus topics.** Plumbed (`node_version.description`, folded into the embedded
   text) but no ground-truth source publishes them; a catalog-scrape adapter per institution is needed.
3. **SCNS semantics.** Membership is recorded as statutory equivalence; whether a specific receiving
   institution awards credit also depends on `CD_TRANSFERABLE` and on it offering the number. Both are
   kept in evidence; the policy does not yet use them.
4. **TCCNS local titles.** The matrix carries only the common title; local nodes reuse it, flagged in attrs.
5. **Institution identity across sources** is by normalized name; SJSU appears as ASSIST 39 and C-ID 12804
   and matches by name today, but a curated alias table is the right long-term answer.
6. **Joint (AND-group) articulations** are stored with group keys and resolved as `satisfies_jointly`
   edges; the query API does not yet evaluate "has the student completed the whole group".
7. **Instructor evaluation is synthetic on the non-SIS side.** The generator mimics the forms grade files
   and rating sites use, but real files carry typos and encoding damage the resolver has not seen.
8. **Rating-site ingestion** is file-based by design; scraping those sites is a terms-of-service question.
9. **PostgreSQL** path parses and mirrors SQLite, but this machine has no server; it is untested live.
