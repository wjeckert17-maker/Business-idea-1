"""Thin DB layer. SQLite by default; PostgreSQL via psycopg with the same SQL (`?` -> `%s`, JSON wrapped)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import CODE_VERSION
from .models import AssertionRecord, CourseRef, CoverageRecord, InstitutionRef, MentionRecord
from .schema import ddl
from .text import guess_institution_kind, institution_name_key, norm_number, norm_subject, stable_hash

JSON_COLUMNS = {"params", "stats", "attrs", "evidence", "mention_ids", "payload", "feature_names", "weights",
                "metrics", "support", "vetoes", "parsed", "courses", "terms", "extra", "profile", "explanation"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def node_key_for(ref: CourseRef, institution_id: Optional[int]) -> str:
    subj, num = norm_subject(ref.subject), norm_number(ref.number)
    if ref.is_hub:
        return f"hub:{ref.system_code}:{subj}:{num}"
    return f"course:{institution_id}:{subj}:{num}"


class Store:
    def __init__(self, conn, dialect: str = "sqlite"):
        self.conn = conn
        self.dialect = dialect
        self._inst_cache: Dict[Tuple[str, str], int] = {}
        self._node_cache: Dict[str, int] = {}
        self._latest_version: Dict[int, Tuple] = {}

    # -- construction -----------------------------------------------------
    @classmethod
    def sqlite(cls, path: str) -> "Store":
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return cls(conn, "sqlite")

    @classmethod
    def postgres(cls, dsn: str) -> "Store":
        import psycopg
        return cls(psycopg.connect(dsn), "postgres")

    def create_schema(self) -> None:
        if self.dialect == "sqlite":
            self.conn.executescript(ddl("sqlite"))
        else:
            with self.conn.cursor() as cur:
                cur.execute(ddl("postgres"))
        self.conn.commit()
        self.ensure_source("human", "Human correction", "human", 1.0)
        self.ensure_source("matcher", "Similarity matcher", "matcher", 1.0)

    # -- SQL helpers --------------------------------------------------------
    def _sql(self, sql: str) -> str:
        return sql if self.dialect == "sqlite" else sql.replace("?", "%s")

    def _j(self, v):
        if v is None:
            return None
        if self.dialect == "sqlite":
            return json.dumps(v, default=str)
        from psycopg.types.json import Jsonb
        return Jsonb(v)

    def execute(self, sql: str, params: Sequence = ()):
        return self.conn.execute(self._sql(sql), tuple(params))

    def executemany(self, sql: str, rows: Iterable[Sequence]) -> None:
        self.conn.executemany(self._sql(sql), list(rows))

    def query(self, sql: str, params: Sequence = ()) -> List[Dict[str, Any]]:
        cur = self.execute(sql, params)
        cols = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            for c in cols:
                if c in JSON_COLUMNS and isinstance(d[c], str):
                    try:
                        d[c] = json.loads(d[c])
                    except ValueError:
                        pass
            out.append(d)
        return out

    def insert(self, table: str, row: Dict[str, Any]) -> int:
        cols = list(row)
        vals = [self._j(row[c]) if c in JSON_COLUMNS else row[c] for c in cols]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        if self.dialect == "postgres":
            pk = {"import_run": "run_id", "resolution_run": "run_id"}.get(table, table + "_id")
            sql += f" RETURNING {pk}"
            cur = self.execute(sql, vals)
            return cur.fetchone()[0]
        cur = self.execute(sql, vals)
        return cur.lastrowid

    def commit(self) -> None:
        self.conn.commit()

    # -- sources / runs -----------------------------------------------------
    def ensure_source(self, code: str, name: str, kind: str, base_trust: float, notes: str = None) -> None:
        if self.dialect == "sqlite":
            self.execute("INSERT OR IGNORE INTO source (source_code, name, kind, base_trust, notes) VALUES (?,?,?,?,?)",
                         (code, name, kind, base_trust, notes))
        else:
            self.execute("INSERT INTO source (source_code, name, kind, base_trust, notes) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                         (code, name, kind, base_trust, notes))
        self.conn.commit()

    def start_run(self, source_code: str, params: Dict[str, Any] = None) -> int:
        return self.insert("import_run", {"source_code": source_code, "started_at": now_iso(),
                                          "code_version": CODE_VERSION, "params": params or {}})

    def finish_run(self, run_id: int, stats: Dict[str, Any]) -> None:
        self.execute("UPDATE import_run SET finished_at = ?, stats = ? WHERE run_id = ?", (now_iso(), self._j(stats), run_id))
        self.conn.commit()

    # -- institutions ---------------------------------------------------------
    def institution_id(self, ref: InstitutionRef) -> int:
        key = (ref.source_code, ref.external_key)
        if key in self._inst_cache:
            return self._inst_cache[key]
        row = self.query("SELECT institution_id FROM institution_key WHERE source_code = ? AND external_key = ?", key)
        if row:
            iid = row[0]["institution_id"]
        else:
            nk = institution_name_key(ref.name)
            row = self.query("SELECT institution_id FROM institution WHERE name_key = ?", (nk,))
            if row:
                iid = row[0]["institution_id"]
            else:
                iid = self.insert("institution", {"canonical_name": ref.name.strip(), "name_key": nk, "state": ref.state,
                                                  "kind": ref.kind or guess_institution_kind(ref.name)})
            self.insert("institution_key", {"source_code": ref.source_code, "external_key": ref.external_key,
                                            "institution_id": iid, "raw_name": ref.name})
        self._inst_cache[key] = iid
        return iid

    def alias_institution(self, source_code: str, external_key: str, institution_id: int, raw_name: str = None) -> None:
        """Human/adapter-supplied: 'this source's key X is institution N'."""
        self.execute("DELETE FROM institution_key WHERE source_code = ? AND external_key = ?", (source_code, external_key))
        self.insert("institution_key", {"source_code": source_code, "external_key": external_key,
                                        "institution_id": institution_id, "raw_name": raw_name})
        self._inst_cache[(source_code, external_key)] = institution_id

    # -- nodes ------------------------------------------------------------------
    def node_id(self, ref: CourseRef, source_code: str, run_id: Optional[int], record_version: bool = True) -> int:
        iid = None if ref.is_hub else self.institution_id(ref.institution)
        key = node_key_for(ref, iid)
        nid = self._node_cache.get(key)
        if nid is None:
            row = self.query("SELECT node_id FROM node WHERE node_key = ?", (key,))
            if row:
                nid = row[0]["node_id"]
            else:
                nid = self.insert("node", {"node_type": "hub" if ref.is_hub else "course", "institution_id": iid,
                                           "system_code": ref.system_code, "subject": norm_subject(ref.subject),
                                           "number": ref.number.strip(), "number_key": norm_number(ref.number), "node_key": key})
            self._node_cache[key] = nid
        if record_version and (ref.title or ref.description or ref.units_min is not None or ref.status):
            sig = (source_code, ref.effective_from, ref.effective_to, ref.title, ref.description, ref.units_min, ref.units_max, ref.status)
            if self._latest_version.get(nid) != sig:
                existing = self.query(
                    "SELECT 1 FROM node_version WHERE node_id = ? AND source_code = ? AND effective_from IS ? AND title IS ? AND status IS ? LIMIT 1"
                    if self.dialect == "sqlite" else
                    "SELECT 1 FROM node_version WHERE node_id = ? AND source_code = ? AND effective_from IS NOT DISTINCT FROM ? AND title IS NOT DISTINCT FROM ? AND status IS NOT DISTINCT FROM ? LIMIT 1",
                    (nid, source_code, ref.effective_from, ref.title, ref.status))
                if not existing:
                    self.insert("node_version", {"node_id": nid, "source_code": source_code, "run_id": run_id,
                                                 "effective_from": ref.effective_from, "effective_to": ref.effective_to,
                                                 "title": ref.title, "description": ref.description, "units_min": ref.units_min,
                                                 "units_max": ref.units_max, "status": ref.status, "attrs": ref.attrs,
                                                 "recorded_at": now_iso()})
                self._latest_version[nid] = sig
        return nid

    # -- assertions -----------------------------------------------------------
    def add_assertion(self, rec: AssertionRecord, source_code: str, run_id: int) -> Optional[int]:
        f = self.node_id(rec.from_ref, source_code, run_id)
        t = self.node_id(rec.to_ref, source_code, run_id)
        dedup = stable_hash(str(f), str(t), rec.relation, source_code, rec.source_ref, rec.effective_from or "", rec.group_key or "")
        if self.query("SELECT 1 FROM assertion WHERE dedup_key = ?", (dedup,)):
            return None
        return self.insert("assertion", {"from_node_id": f, "to_node_id": t, "relation": rec.relation, "confidence": rec.confidence,
                                         "source_code": source_code, "run_id": run_id, "source_ref": rec.source_ref,
                                         "asserted_at": now_iso(), "effective_from": rec.effective_from, "effective_to": rec.effective_to,
                                         "group_key": rec.group_key, "evidence": rec.evidence, "dedup_key": dedup})

    def add_assertions_bulk(self, rows: List[Dict[str, Any]]) -> int:
        """rows already carry node ids; used by the matcher. Skips dedup collisions."""
        n = 0
        for r in rows:
            r.setdefault("asserted_at", now_iso())
            if self.query("SELECT 1 FROM assertion WHERE dedup_key = ?", (r["dedup_key"],)):
                continue
            self.insert("assertion", r)
            n += 1
        return n

    def add_coverage(self, rec: CoverageRecord, source_code: str, run_id: int) -> None:
        f, t = self.institution_id(rec.from_institution), self.institution_id(rec.to_institution)
        if self.query("SELECT 1 FROM coverage WHERE source_code = ? AND from_institution_id = ? AND to_institution_id = ? AND effective_from IS ?"
                      if self.dialect == "sqlite" else
                      "SELECT 1 FROM coverage WHERE source_code = ? AND from_institution_id = ? AND to_institution_id = ? AND effective_from IS NOT DISTINCT FROM ?",
                      (source_code, f, t, rec.effective_from)):
            return
        self.insert("coverage", {"source_code": source_code, "from_institution_id": f, "to_institution_id": t,
                                 "effective_from": rec.effective_from, "effective_to": rec.effective_to, "run_id": run_id})

    # -- corrections ------------------------------------------------------------
    def add_correction(self, author: str, domain: str, action: str, note: str = None, **kw) -> int:
        row = {"author": author, "created_at": now_iso(), "domain": domain, "action": action, "note": note}
        row.update(kw)
        cid = self.insert("correction", row)
        self.conn.commit()
        return cid

    # -- mentions -----------------------------------------------------------------
    def add_mention(self, m: MentionRecord, run_id: int, parsed: Dict[str, Any]) -> Optional[int]:
        iid = self.institution_id(m.institution) if m.institution else None
        dedup = stable_hash(m.source_code, str(iid), m.raw_name, m.email or "", m.external_id or "", m.department or "",
                            ",".join(sorted(m.courses)), ",".join(sorted(m.terms)), m.source_ref or "")
        if self.query("SELECT mention_id FROM mention WHERE dedup_key = ?", (dedup,)):
            return None
        return self.insert("mention", {"source_code": m.source_code, "run_id": run_id, "institution_id": iid, "raw_name": m.raw_name,
                                       "parsed": parsed, "email": (m.email or None), "external_id": m.external_id, "department": m.department,
                                       "courses": sorted(set(m.courses)), "terms": sorted(set(m.terms)), "role": m.role, "extra": m.extra,
                                       "observed_at": m.observed_at, "source_ref": m.source_ref, "dedup_key": dedup})

    # -- lookups ------------------------------------------------------------------
    def find_node(self, institution_name: str, subject: str, number: str) -> Optional[Dict[str, Any]]:
        nk = institution_name_key(institution_name)
        rows = self.query("""SELECT n.*, i.canonical_name FROM node n JOIN institution i USING (institution_id)
                             WHERE i.name_key = ? AND n.subject = ? AND n.number_key = ?""",
                          (nk, norm_subject(subject), norm_number(number)))
        return rows[0] if rows else None

    def node_label(self, node_id: int) -> str:
        r = self.query("""SELECT n.node_type, n.subject, n.number, n.system_code, i.canonical_name AS inst,
                                 (SELECT title FROM node_version v WHERE v.node_id = n.node_id ORDER BY version_id DESC LIMIT 1) AS title
                          FROM node n LEFT JOIN institution i USING (institution_id) WHERE n.node_id = ?""", (node_id,))
        if not r:
            return f"node {node_id}"
        r = r[0]
        who = r["inst"] if r["node_type"] == "course" else f"{r['system_code'].upper()} hub"
        return f"{who}: {r['subject']} {r['number']}" + (f" — {r['title']}" if r["title"] else "")
