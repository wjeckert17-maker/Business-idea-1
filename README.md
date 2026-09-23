# Course Planner

A course-planning system for university students: it ingests a school's live section feed, resolves courses and
instructors across institutions, turns catalog requirement text into a machine-readable tree, scores sections on
teaching quality and difficulty with honest confidence, and builds the five best schedules for a student's own
weights — then explains every recommendation and measures itself before anything ships.

Everything here was built ground-truth-first and evaluated against held-out data. Where a component does not yet
meet its bar, the README says so rather than the code pretending otherwise.

## How the pieces fit

```
 registrar feed ──► course_ingest ──► PostgreSQL schema ◄── reqx (catalog text → requirement tree)
 (Banner 9)            │                    │
                       ▼                    ▼
 ASSIST / SCNS /   canon: canonical      scoring: quality / difficulty / confidence
 TCCNS / C-ID ───► course + instructor       │
                   graph (bitemporal)        ▼
                                         sched: CP-SAT top-5 schedules, per-course weights, explanations
                                             │
                             ┌───────────────┼──────────────────┐
                             ▼               ▼                  ▼
                         web (Next.js)   extension (MV3)     evalx (gates, replays, diffs)
```

| component | what it does | status |
|---|---|---|
| `schema.sql`, `schema_additions.sql`, `DESIGN_NOTES.md` | PostgreSQL model: institutions, terms, courses (versioned by catalog year), sections, meeting patterns with fast conflict slots, grade distributions, recursive requirement trees, plans, constraints | DDL parses against PG17; not yet run on a live server |
| `course_ingest/` | Polite, idempotent section ingestion. Banner 9 adapter; one TOML per school; run summary with unrecognized patterns | 12,190 real sections parsed, 0 failures |
| `canon/` + `CANON_DESIGN.md` | Cross-institution course-equivalence graph (directed, weighted, provenance on every edge, as-of queries, human corrections replayed) and instructor identity resolution | ground truth loaded from 4 sources; see *Measured results* |
| `reqx/` | Catalog HTML/PDF → requirement tree via a strict-schema model call; validator rejects fabricated codes, unknown courses, unsatisfiable groups, credit mismatches; ambiguity becomes NEEDS_REVIEW | extractor + validator exercised on UC Davis CS BS; live model call needs an API key |
| `scoring/` | Pure functions: recency decay → Bayesian shrinkage → leniency-adjusted quality residual → within-department difficulty percentile; components and confidence on every score; sentiment can be weighted to zero | 12 synthetic-corpus tests incl. the lenient-grader case |
| `sched/` | OR-Tools CP-SAT scheduler: hard constraints, per-course weight vectors, top-5 distinct course sets, runner-up per slot, minimum-cost relaxation when infeasible | 16 fixture tests |
| `web/` | Mobile-first Next.js UI: onboarding with transcript parsing, drag-to-block week grid, per-course "learn it / survive it", swipeable results, registration plan with backups | all four screens driven at 375 px against the live engine |
| `extension/` | Chrome MV3 extension: planner scores on the registrar's class search by CRN; opt-in, hover-only, client-side third-party ratings that never leave the browser; privacy disclosure | selectors verified on the live Banner page |
| `evalx/` | Requirement agreement vs official audits (100% gate), schedule replay vs real registrations, leniency sanity and demographic skew audit, versioned replays with diff | harness proven on synthetic fixtures; real student data to be supplied |

## Quick start

Python 3.9+ and Node 18+.

```bash
# Python side
pip install -e ".[dev]"                 # requests, psycopg, numpy, xlrd, ortools, jsonschema, pypdf, pytest
pip install -e ".[embeddings]"          # optional: sentence-transformers for the MiniLM matcher
pytest                                  # 81 tests, ~3 s, no network, no database

# Database (optional; the engines run on SQLite / pure Python without it)
psql "$DSN" -f schema.sql -f schema_additions.sql

# Ingest a term of sections
python -m course_ingest --config schools/gatech.toml --term 202608 --dry-run --limit 50
python -m course_ingest --config schools/gatech.toml --term 202608 --dsn "$DSN"

# Build the canonical graph (raw caches come from tools/fetch_*.py)
python -m canon --db canon.db init
python -m canon --db canon.db ingest --source scns --path data/scns/crslist.txt --option institutions=data/scns/institutions.json
python -m canon --db canon.db split && python -m canon --db canon.db train --embedder minilm
python -m canon --db canon.db resolve
python -m canon --db canon.db query "Evergreen Valley College | CS | 22A" --as-of 2023-10-15

# Parse a catalog page into a requirement tree
python -m reqx show-prompt examples/ucdavis_cs_bs.html --section 1
ANTHROPIC_API_KEY=... python -m reqx run examples/ucdavis_cs_bs.html --courses examples/ucd_courses.json

# Web app
cd web && npm install && PLANNER_PYTHON=python3 npm run dev     # http://localhost:3000
```

Each package has its own README with the full command surface: `course_ingest/README.md`, `canon/README.md`,
`reqx/README.md`, `scoring/README.md`, `sched/README.md`, `web/README.md`, `extension/README.md`, `evalx/README.md`.

## Data: what is real and what is not

* **Real:** Georgia Tech Fall/Summer 2026 sections (times, rooms, CRNs, instructors, seat counts) from the public Banner
  feed; ASSIST articulation agreements (279, SJSU and UC Davis as receivers); the complete Florida SCNS course
  inventory (193k rows); the Texas TCCNS matrix; all 27,650 C-ID approvals; UC Davis's CS BS catalog page and
  1,479 of its course listings.
* **Synthetic, and labelled as such wherever it appears:** grade distributions, ratings, syllabus features, student
  transcripts, audits, registrations and instructor demographics. No real student or grade data is in this repository.

All fetchers identify themselves with a contact address, respect robots.txt, and run at one request per second or
slower. Raw payloads are cached with fetch timestamps so every derived record can be traced to its source.

## Measured results

Numbers below are from the reports checked into the repo (`matcher_eval.md`, `people_eval.md`, `eval/`).

* **Instructor identity:** on 2,334 real instructors plus generated grade-feed, rating-site and syllabus mentions,
  pairwise precision 1.000 with **zero wrong merges** and recall 0.996; 386 partial-name mentions deliberately left
  separate as ambiguous or unanchored.
* **Course-equivalence matcher:** with candidates scoped to coverage, only 220 of ~2,400 holdout positives are missed by
  generation, but the title-only scorer cannot reach the 98% precision bar at useful recall — precision 1.000 at recall
  0.02 under the chosen thresholds, 0.69 at recall 0.36. **This is not shipped as inference**: the resolver admits
  inferred edges only above those thresholds, so the graph today is verified ground truth plus hub derivation.
  What would move it is in `CANON_DESIGN.md` §3.
* **Scoring sanity (synthetic):** raw rating vs GPA r = +0.55, leniency-adjusted quality vs GPA r = −0.02.
* **Requirement gate:** 100% agreement required; the sample run in `eval/requirements.md` is deliberately blocked
  by one corrupted audit to show the side-by-side dump.

## Design rules that run through everything

* **Ground truth first, then a fixed holdout, then models.** Nothing inferred ships unmeasured.
* **Never fabricate.** Requirement parsing emits NEEDS_REVIEW with the verbatim catalog text instead of guessing;
  the validator fails any course code that is not in the source.
* **Provenance and time on every fact.** Edges carry source, reference, confidence and both catalog time and
  system time; corrections are append-only and always outrank inference.
* **Confidence is a first-class output.** Every score says how much of it came from data rather than a prior, and the
  UI prints sample sizes next to every figure.
* **Instructor data is context, not a leaderboard.** Section history is shown with n; no letter grades for people.
* **Adding a school or a source is an adapter, never a core edit.**

## Repository layout

```
schema.sql  schema_additions.sql  DESIGN_NOTES.md  CANON_DESIGN.md   data model and design records
course_ingest/  schools/         ingestion job and per-school configs
canon/  tools/                   canonical graph engine and the raw-data fetchers
reqx/  examples/                 requirement parser and its sample inputs / exact prompt
scoring/  sched/                 scoring service and scheduler
web/  extension/                 UI and browser extension
evalx/  eval/                    evaluation harness and sample reports
tests/                           81 tests across all packages
matcher_eval.md  people_eval.md  evaluation reports for the graph engine
```

## Known gaps

1. The PostgreSQL loaders parse and mirror SQLite but have not run against a live server.
2. `reqx` has not made a live model call from this environment; the prompt, schema and validator are exercised, the
   model output is not.
3. The matcher needs course descriptions or a second-stage judge before its inferred edges are worth admitting.
4. `evalx` has run only on synthetic students; the real transcripts, audits and registrations are yours to supply.
5. The extension's rating-site adapter targets a site whose terms restrict automated access; confirm with counsel
   before enabling it, and note it is isolated to one file.

## License

Proprietary — all rights reserved. See `LICENSE`. Bundled dependencies and cached public data sets keep their own terms.
