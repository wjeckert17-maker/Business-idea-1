# evalx — evaluation harness

Three evaluations, a versioned replay, and a diff. **Real transcripts, audits and registrations are yours to
supply** in the formats below; the bundled fixtures are synthetic (real section rows, generated everything else)
and exist to prove the harness, not the product.

```bash
python -m evalx fixture --demo web/data/demo.json --out-dir eval/fixtures          # synthetic inputs, one audit corrupted on purpose
python -m evalx requirements --transcripts t.jsonl --audits a.jsonl --trees trees.json --out eval/req   # exit 1 unless 100%
python -m evalx schedules --corpus scored_corpus.json --registrations r.jsonl --out eval/sched
python -m evalx sanity --corpus term_corpus.json [--demographics d.json] --out eval/sanity              # exit 1 if adjustment fails
python -m evalx replay --corpus term_corpus.json --version v7 --m 15 --decay 0.3 --sentiment-weight 1 --out-dir runs
python -m evalx diff runs/v6_*.json runs/v7_*.json
```

## 1. Requirement correctness (`requirements_eval.py`)
* `transcripts.jsonl`: `{"student_id", "program", "catalog_year", "completed": ["CS 1301", ...], "credits"?: {code: units}}`
* `audits.jsonl`: `{"student_id", "groups": [{"title", "satisfied": bool, "remaining": [codes]}]}` — the university's statement.
* `trees.json`: `{catalog_year: tree}` in the requirement-tree node format (reqx output / step-1 schema).

Groups are matched by title. A group agrees when satisfied/unsatisfied matches and, if unsatisfied, the remaining
course lists match. `needs_review` and unresolved `filter` leaves make a group *undecidable*, never satisfied.
Every disagreement is dumped with the catalog text stored on the node (description, source lines, NEEDS_REVIEW text)
beside our parse. **Gate: 100%**; the command exits non-zero otherwise.

## 2. Schedule preference (`schedule_eval.py`)
* `registrations.jsonl`: `{"student_id", "completed": [...], "crns": [...], "min_credits"?, "max_credits"?, "blocked"?: [{weekday,start,end}], "allow_outside"?: bool}`
* corpus: sections with `quality`/`difficulty`/`history` (the web app's `demo.json` shape, or a replay-scored corpus).

Per student: their course set's rank in our top 5 (and the exact section set's rank), instructor GPA of our #1 vs
theirs with how many sections had history, and time conflicts in each. Constraints default to their actual credit
load ±1 with no blocked time; pass the real ones when you have them — without them the comparison flatters the student.

## 3. Scoring sanity (`scoring_sanity.py`)
Pearson r between raw rating and mean GPA (expected > 0.2), then between adjusted quality and mean GPA
(expected |r| ≤ 0.1). Verdict `BLOCK` when the second fails. With `demographics` (`{section_key: {attribute: group}}`)
it reports, per attribute and group: n, mean adjusted quality, mean difficulty, the quality delta against everyone
else, and a permutation-test p-value — printed whether or not it is flattering.

## 4. Replay and diff (`replay.py`)
A term corpus holds raw scoring inputs, unscored sections, registrations and (optionally) demographics. `replay`
scores them under the given `ScoringConfig`, runs evaluations 2 and 3, and writes
`runs/<version>_<timestamp>.json` with the config, code version and a corpus hash. `diff` reports aggregate deltas,
which students' #1 changed, hits gained/lost, and the Spearman correlation of section quality between versions,
and states one of: *no change*, *just different*, or *better on … / worse on …*. Runs on different corpora are flagged.
