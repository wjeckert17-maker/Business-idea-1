# course-ingest

Pulls a full term of sections from a university class-search system and loads it into the
course-planning schema (`schema.sql` + `schema_additions.sql`).

```
course_ingest/
  http.py            PoliteSession: honest User-Agent, robots.txt, 1 req/s, exponential backoff
  models.py          normalized records every adapter emits (SectionRecord, MeetingRecord, ...)
  meetings.py        meeting-pattern normalization shared by all adapters
  adapters/base.py   SourceAdapter contract + registry
  adapters/banner9.py  Ellucian Banner 9 StudentRegistrationSsb JSON API
  loader.py          idempotent writes keyed on natural keys
  runner.py          per-term orchestration, failure isolation, run summary
  cli.py             `python -m course_ingest ...`
schools/gatech.toml  one school = one config (URL, contact, code maps)
```

## Run

```bash
pip install -e ".[dev]"
psql "$COURSE_INGEST_DSN" -f schema.sql -f schema_additions.sql
python -m course_ingest --config schools/gatech.toml --list-terms
python -m course_ingest --config schools/gatech.toml --term 202608 --dry-run --limit 50   # smoke test, no DB
python -m course_ingest --config schools/gatech.toml --term 202608 --dsn "$COURSE_INGEST_DSN"
```

The run prints a JSON summary (seen / inserted / updated / unchanged / failed / missing, request
and retry counts, unrecognized meeting patterns, unmapped codes). Failed sections are written with
their raw payload to `failures-<school>-<term>-<timestamp>.jsonl`; the file is deleted if empty.
Exit code 1 means the run was incomplete or errored, 2 means a term code was unknown.

## Adding a school

* Another Banner 9 school: copy `schools/gatech.toml`, change `base_url`, `slug`, `contact`, and the
  code maps. Run once with `--dry-run`; `unmapped_codes` in the summary tells you what to add.
* A different SIS: subclass `SourceAdapter` in `course_ingest/adapters/<name>.py`, implement
  `list_terms`, `fetch_sections`, `parse_section`, decorate with `@register`, import it in
  `adapters/__init__.py`. Use `meetings.normalize_meeting` so the same pattern rules apply.
  Nothing in `loader.py` or `runner.py` changes.

## Idempotency

* `section` keyed on `(term_id, crn)`; only changed columns are written, `last_seen_at` always.
* Enrollment history: one `section_enrollment_snapshot` row when the counters or status change.
* Instructors: temporal `section_instructor` rows; a change closes the old range and opens a new one.
* Meeting blocks: replaced as a set only when the set differs (slots rebuild via trigger).
* Courses: `(catalog_year, subject, number)`; an unknown code reuses the `course_id` of the same
  code in an earlier catalog year, otherwise a new course is created and tagged in `course.notes`.
* Sections in the DB but absent from a *complete* run are counted as `missing`; with
  `--mark-missing-cancelled` they are set to `cancelled` and snapshotted.

## Tests

```bash
pytest
```
