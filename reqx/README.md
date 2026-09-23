# reqx — catalog requirements → requirement tree

```
reqx/extract.py   step 1  HTML (CourseLeaf) / PDF / pasted text -> Outline (headings, areas, sub-areas, verbatim rows)
reqx/prompt.py    step 2  the system prompt + strict JSON schema sent to the model (printable with `show-prompt`)
reqx/llm.py       step 2  AnthropicRunner (claude-opus-5, structured output), ReplayRunner (saved responses), DryRunRunner
reqx/validate.py  step 3-4  source-fidelity, Course-table existence, credit totals, satisfiability, confidence, review flags
reqx/emit.py      output  step-1 schema rows (program_version, requirement_group, requirement, course_list) + review_queue
reqx/courses.py   Course table loaders: JSON, PostgreSQL (course_catalog_entry), CourseLeaf subject pages
```

```bash
python -m reqx outline examples/ucdavis_cs_bs.html                 # what step 1 extracted, as the model will see it
python -m reqx show-prompt examples/ucdavis_cs_bs.html --section 1 # the exact system prompt, user message and schema
python -m reqx courses data/catalog/ucd_courses --out ucd_courses.json
python -m reqx run examples/ucdavis_cs_bs.html --courses ucd_courses.json --runner anthropic \
       --program-code UCD-CS-BS --catalog-year 2026-2027 --out requirements.json --report requirements_report.md
python -m reqx run ... --runner replay      # re-validate from saved model responses, no network
python -m reqx run ... --runner dry-run     # print the first request and stop
```

Credentials: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile. Every model
response is saved under `--cache-dir` with its request id, model and token usage, so a run is
reproducible and auditable.

## What the validator refuses

| finding | severity | meaning |
|---|---|---|
| `fabricated_code` | error | a course code in the tree does not occur in that section's text |
| `unknown_course` | error | the code (and none of its cross-listed aliases) is in the Course table |
| `unsatisfiable` | error | `any_n` asks for more children than exist; `credits` asks for more than the children can supply |
| `empty_group`, `bad_operator`, `bad_min_count`, `missing_code`, `empty_list` | error | structural |
| `review_text_not_verbatim`, `review_without_text` | error | a NEEDS_REVIEW node must quote the catalog exactly |
| `credit_mismatch` / `degree_total_mismatch` | error | stated subtotal / degree total outside the tree's computed range |
| `units_not_in_source` | warning | a unit figure the catalog does not show |
| `unescalated_ambiguity` | warning | the section says "advisor", "approved substitute", "petition", ... and no NEEDS_REVIEW node quotes it |
| `vague_filter` | warning | a filter leaf with no subject codes |

Confidence per group = model confidence × penalties for the findings above; any NEEDS_REVIEW node caps
it at 0.8; below 0.8, or any error, or any NEEDS_REVIEW → the group goes to `review_queue`. The run exits
non-zero when anything is an error, so nothing loads silently.

## Schema requirements

`schema_additions.sql` adds `requirement_group.needs_review`, `requirement.source_text` and
`requirement.aliases`. A NEEDS_REVIEW group has no children and `needs_review = true`; the planner must
treat it as unsatisfied until a human replaces it.
