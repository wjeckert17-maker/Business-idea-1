# canon

Cross-institution course-equivalence and instructor-identity graph. See `../CANON_DESIGN.md` for the
design and the evaluation protocol; `../matcher_eval_*.md` and `../people_eval.md` for measured results.

```bash
pip install numpy xlrd requests            # sentence-transformers optional (--embedder minilm)
export CANON_DB=canon.db                   # or a postgres:// DSN
python -m canon init
python -m canon sources                    # registered ground-truth adapters

# 1. ground truth (raw caches written by the fetch scripts in tools/)
python -m canon ingest --source assist --path data/assist
python -m canon ingest --source cid    --path data/cid
python -m canon ingest --source tccns  --path data/tccns/matrix_2025.xls --option year=2025
python -m canon ingest --source scns   --path data/scns/crslist.txt --option institutions=data/scns/institutions.json

# 2. holdout, fixed by salted hash, before any modelling
python -m canon split --salt v1 --holdout-pct 20

# 3. matcher: train on the train split, choose threshold on train-validation, report on holdout
python -m canon train --embedder minilm --target-precision 0.98 --report matcher_eval.md
python -m canon infer --embedder minilm --scope assist        # writes matcher assertions (never edges)

# 4. resolve into directed, weighted, bitemporal edges; query and explain
python -m canon resolve
python -m canon query "Evergreen Valley College | CS | 22A" --as-of 2023-10-15
python -m canon explain "Evergreen Valley College | MATH | 071" "San Jose State University | MATH | 30"
python -m canon correct --author registrar@sjsu.edu --action reject "Evergreen Valley College | MATH | 071" "San Jose State University | MATH | 30" --note "revised 2024"
python -m canon resolve                                        # corrections replayed, always win

# instructors
python -m canon people-ingest --source sis --path data/gatech --institution "Georgia Institute of Technology"
python -m canon people-ingest --source grade_feed --path grades.csv --institution "Georgia Institute of Technology"
python -m canon people-resolve --threshold 0.6 --margin 0.15
python -m canon people-explain 1234
python -m canon people-correct --author me --action cannot_link 1234 5678
python -m canon people-evaluate --path data/gatech --institution "Georgia Institute of Technology"
```

Layout: `schema.py` (DDL, sqlite/postgres), `store.py`, `models.py`, `text.py`, `sources/` (assist, scns, tccns, cid),
`ingest.py`, `split.py`, `embed.py`, `matcher.py`, `evaluate.py`, `resolve.py`, `people/` (names, mentions, resolver, evaluate), `cli.py`.
