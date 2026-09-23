"""Portable DDL. Works on SQLite (default, zero-install) and PostgreSQL (same statements, two
substitutions). Every table is append-only or bitemporal; nothing that carries provenance is ever
UPDATEd in place except to close a system-time interval.

Time model
  effective_from / effective_to   catalog time: when the fact is true in the world ("under the 2023 catalog").
                                  ISO dates, half-open [from, to), NULL = unbounded.
  system_from / system_to         transaction time: when *we* believed it. NULL system_to = current.
"""

DDL = """
CREATE TABLE IF NOT EXISTS source (
  source_code   TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  kind          TEXT NOT NULL,          -- articulation | common_numbering | sis | grade_feed | syllabus | rating_site | matcher | human
  base_trust    REAL NOT NULL,          -- multiplier applied to every assertion from this source
  notes         TEXT
);
CREATE TABLE IF NOT EXISTS import_run (
  run_id        {PK},
  source_code   TEXT NOT NULL REFERENCES source,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  code_version  TEXT NOT NULL,
  params        {JSON},
  stats         {JSON}
);
CREATE TABLE IF NOT EXISTS institution (
  institution_id  {PK},
  canonical_name  TEXT NOT NULL,
  name_key        TEXT NOT NULL UNIQUE,   -- normalized name; adapters may override via institution_key
  state           TEXT,
  kind            TEXT                     -- community_college | university | other
);
CREATE TABLE IF NOT EXISTS institution_key (
  source_code     TEXT NOT NULL,
  external_key    TEXT NOT NULL,
  institution_id  INTEGER NOT NULL REFERENCES institution,
  raw_name        TEXT,
  PRIMARY KEY (source_code, external_key)
);
-- A node is a course identity: (institution, subject, number) for real courses, or (system, subject,
-- number) for a hub = a common-numbering descriptor (SCNS statewide number, TCCNS number, C-ID).
CREATE TABLE IF NOT EXISTS node (
  node_id         {PK},
  node_type       TEXT NOT NULL,           -- course | hub
  institution_id  INTEGER REFERENCES institution,
  system_code     TEXT,
  subject         TEXT NOT NULL,
  number          TEXT NOT NULL,
  number_key      TEXT NOT NULL,
  node_key        TEXT NOT NULL UNIQUE
);
-- What a node looked like according to a source at a point in catalog time. Append-only.
CREATE TABLE IF NOT EXISTS node_version (
  version_id      {PK},
  node_id         INTEGER NOT NULL REFERENCES node,
  source_code     TEXT NOT NULL,
  run_id          INTEGER,
  effective_from  TEXT,
  effective_to    TEXT,
  title           TEXT,
  description     TEXT,
  units_min       REAL,
  units_max       REAL,
  status          TEXT,
  attrs           {JSON},
  recorded_at     TEXT NOT NULL
);
-- One claim by one source about one directed pair. Never updated, never deleted.
CREATE TABLE IF NOT EXISTS assertion (
  assertion_id    {PK},
  from_node_id    INTEGER NOT NULL REFERENCES node,
  to_node_id      INTEGER NOT NULL REFERENCES node,
  relation        TEXT NOT NULL,           -- satisfies | satisfies_jointly | member_of | denied | not_articulated
  confidence      REAL NOT NULL,           -- the source's own confidence, before base_trust
  source_code     TEXT NOT NULL REFERENCES source,
  run_id          INTEGER,
  source_ref      TEXT,                    -- URL / agreement key / file+line: what to show a registrar
  asserted_at     TEXT NOT NULL,
  effective_from  TEXT,
  effective_to    TEXT,
  group_key       TEXT,                    -- joint (AND) groups share a key
  evidence        {JSON},
  dedup_key       TEXT NOT NULL UNIQUE
);
-- Human input. Outranks everything. Replayed on every resolution.
CREATE TABLE IF NOT EXISTS correction (
  correction_id   {PK},
  author          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  domain          TEXT NOT NULL,           -- course | person
  action          TEXT NOT NULL,           -- course: assert | reject   person: must_link | cannot_link
  from_node_id    INTEGER,
  to_node_id      INTEGER,
  relation        TEXT,
  confidence      REAL,
  effective_from  TEXT,
  effective_to    TEXT,
  mention_ids     {JSON},
  note            TEXT,
  payload         {JSON}
);
-- Which pairs are held out from training. Fixed by hash so retrains are comparable.
CREATE TABLE IF NOT EXISTS eval_split (
  pair_key        TEXT PRIMARY KEY,
  split           TEXT NOT NULL,           -- train | holdout
  salt            TEXT NOT NULL
);
-- Which (from_institution, to_institution, period) pairs a ground-truth source covers exhaustively,
-- i.e. absence of an assertion there is a real negative.
CREATE TABLE IF NOT EXISTS coverage (
  coverage_id     {PK},
  source_code     TEXT NOT NULL,
  from_institution_id INTEGER NOT NULL,
  to_institution_id   INTEGER NOT NULL,
  effective_from  TEXT,
  effective_to    TEXT,
  run_id          INTEGER
);
CREATE TABLE IF NOT EXISTS matcher_model (
  model_id        {PK},
  trained_at      TEXT NOT NULL,
  embedder        TEXT NOT NULL,
  feature_names   {JSON},
  weights         {JSON},
  threshold       REAL NOT NULL,
  metrics         {JSON},
  params          {JSON}
);
CREATE TABLE IF NOT EXISTS resolution_run (
  run_id          {PK},
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  policy_version  TEXT NOT NULL,
  stats           {JSON}
);
-- The graph as currently believed. Bitemporal; closed rows are kept.
CREATE TABLE IF NOT EXISTS resolved_edge (
  edge_id         {PK},
  from_node_id    INTEGER NOT NULL,
  to_node_id      INTEGER NOT NULL,
  relation        TEXT NOT NULL,           -- satisfies | member_of
  confidence      REAL NOT NULL,
  basis           TEXT NOT NULL,           -- correction | ground_truth | inferred
  effective_from  TEXT,
  effective_to    TEXT,
  support         {JSON},                  -- assertion ids / correction ids behind it
  vetoes          {JSON},
  system_from     TEXT NOT NULL,
  system_to       TEXT,
  resolution_run_id INTEGER NOT NULL,
  edge_key        TEXT NOT NULL
);
-- Instructor identity
CREATE TABLE IF NOT EXISTS mention (
  mention_id      {PK},
  source_code     TEXT NOT NULL,
  run_id          INTEGER,
  institution_id  INTEGER,
  raw_name        TEXT NOT NULL,
  parsed          {JSON},
  email           TEXT,
  external_id     TEXT,
  department      TEXT,
  courses         {JSON},
  terms           {JSON},
  role            TEXT,
  extra           {JSON},
  observed_at     TEXT,
  source_ref      TEXT,
  dedup_key       TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS person (
  person_id       {PK},
  resolution_run_id INTEGER NOT NULL,
  display_name    TEXT NOT NULL,
  profile         {JSON},
  person_key      TEXT NOT NULL,
  system_from     TEXT NOT NULL,
  system_to       TEXT
);
CREATE TABLE IF NOT EXISTS person_mention (
  person_id       INTEGER NOT NULL,
  mention_id      INTEGER NOT NULL,
  confidence      REAL NOT NULL,
  explanation     {JSON},
  system_from     TEXT NOT NULL,
  system_to       TEXT
);
CREATE INDEX IF NOT EXISTS node_inst ON node (institution_id, subject, number_key);
CREATE INDEX IF NOT EXISTS node_version_node ON node_version (node_id, effective_from);
CREATE INDEX IF NOT EXISTS assertion_from ON assertion (from_node_id, relation);
CREATE INDEX IF NOT EXISTS assertion_to ON assertion (to_node_id, relation);
CREATE INDEX IF NOT EXISTS assertion_source ON assertion (source_code, run_id);
CREATE INDEX IF NOT EXISTS coverage_pair ON coverage (from_institution_id, to_institution_id);
CREATE INDEX IF NOT EXISTS resolved_from ON resolved_edge (from_node_id, system_to);
CREATE INDEX IF NOT EXISTS resolved_to ON resolved_edge (to_node_id, system_to);
CREATE INDEX IF NOT EXISTS resolved_key ON resolved_edge (edge_key, system_to);
CREATE INDEX IF NOT EXISTS mention_inst ON mention (institution_id);
CREATE INDEX IF NOT EXISTS person_mention_m ON person_mention (mention_id, system_to);
CREATE INDEX IF NOT EXISTS person_mention_p ON person_mention (person_id, system_to);
"""

DIALECTS = {
    "sqlite": {"{PK}": "INTEGER PRIMARY KEY AUTOINCREMENT", "{JSON}": "TEXT"},
    "postgres": {"{PK}": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY", "{JSON}": "JSONB"},
}


def ddl(dialect: str) -> str:
    out = DDL
    for k, v in DIALECTS[dialect].items():
        out = out.replace(k, v)
    return out
