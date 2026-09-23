# Course planner — data layer

Three deliverables, built in order:

| step | what | where | status |
|---|---|---|---|
| 1 | Data model: PostgreSQL DDL for institutions, terms, courses, sections, meetings, grades, requirement trees, plans, constraints | `schema.sql`, `schema_additions.sql`, `DESIGN_NOTES.md` | parsed against PG17 grammar; not run on a live server |
| 2 | Section ingestion job (Banner 9 adapter, polite HTTP, idempotent loader, run summary) | `course_ingest/`, `schools/`, `course_ingest/README.md` | 12,190 real sections parsed, 0 failures; loader untested against a live Postgres |
| 3 | Normalization engine: course equivalence graph (ground truth → holdout → matcher → directed edges) and instructor identity | `canon/`, `CANON_DESIGN.md`, `matcher_eval_*.md`, `people_eval.md`, `tools/` | run end-to-end on SQLite with real ASSIST / SCNS / TCCNS / C-ID data |
| 5 | Section scoring service: decay → shrinkage → leniency residual → within-department difficulty percentile, with per-input components and effective-n confidence | `scoring/`, `scoring/README.md` | pure functions; 12 synthetic-corpus tests |
| 6 | Schedule engine: CP-SAT, per-course weight vectors, top-5 distinct course sets, explanations with runner-ups, minimum-cost relaxation | `sched/`, `sched/README.md` | 16 fixture tests |
| 7 | Mobile-first web UI (Next.js 15, TypeScript, Tailwind 4): onboarding with transcript parsing, drag-to-block week grid with per-course learn/survive dials, swipeable results, registration plan with CRNs and backup ladder | `web/`, `web/README.md`, `tools/build_demo_data.py` | production build passes; all four screens exercised in a 375px viewport against the live engine |
| 8 | Chrome extension (MV3): badges with planner scores on the registrar's class search by CRN; opt-in, hover-only, rate-limited third-party ratings rendered client-side and never transmitted; privacy disclosure | `extension/`, `extension/PRIVACY.md` | scripts syntax-checked; selectors verified against the live Banner results table; `/api/scores` verified |
| 9 | Evaluation harness: requirement agreement vs official audits (100% gate, side-by-side dumps), schedule replay vs real registrations, leniency sanity + demographic skew audit, versioned replays with diff | `evalx/`, `evalx/README.md`, `eval/` | 8 fixture tests; real student data to be supplied |
| 4 | Catalog requirements → requirement tree: hierarchy-preserving extraction, strict-schema model call, validator that rejects fabrication, NEEDS_REVIEW routing | `reqx/`, `reqx/README.md`, `examples/` | extractor and validator run on the UC Davis CS BS page; the model call needs an API key this machine lacks |

```bash
pip install -e ".[dev]"      # course_ingest deps; canon needs numpy, xlrd (and sentence-transformers for --embedder minilm); sched needs ortools
pytest                       # 81 tests: ingestion, parsers, resolver, names, matcher building blocks
```
