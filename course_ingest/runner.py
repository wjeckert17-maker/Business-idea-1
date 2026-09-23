"""Orchestrates one term: fetch -> parse -> load, never letting one bad section stop the run."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, TextIO

from .adapters.base import SourceAdapter
from .models import ParseError, SectionRecord, TermInfo

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    institution: str
    term_code: str
    term_name: str
    started_at: str
    finished_at: Optional[str] = None
    complete: bool = False               # every page fetched
    dry_run: bool = False
    seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    missing: int = 0                     # in DB for this term but absent from this run
    cancelled_marked: int = 0
    requests_made: int = 0
    retries: int = 0
    unrecognized_meeting_patterns: List[Dict[str, Any]] = field(default_factory=list)
    unmapped_codes: Dict[str, Dict[str, int]] = field(default_factory=dict)
    failures: List[Dict[str, str]] = field(default_factory=list)   # {crn, error}; raw goes to the failures file
    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def _json_default(o):
    return o.isoformat() if hasattr(o, "isoformat") else repr(o)


class TermRun:
    def __init__(self, adapter: SourceAdapter, loader, term: TermInfo, *,
                 failures_file: Optional[TextIO] = None, mark_missing_cancelled: bool = False,
                 limit: Optional[int] = None, now: Optional[datetime] = None):
        self.adapter = adapter
        self.loader = loader                     # None => dry run (parse only)
        self.term = term
        self.failures_file = failures_file
        self.mark_missing_cancelled = mark_missing_cancelled
        self.limit = limit
        self.started_at = now or datetime.now(timezone.utc)
        self.summary = RunSummary(
            institution=adapter.config.slug, term_code=term.code, term_name=term.name,
            started_at=self.started_at.isoformat(), dry_run=loader is None,
        )
        self._unmapped: Dict[str, Counter] = defaultdict(Counter)
        self._span: Optional[List[date]] = None

    # -- main loop --------------------------------------------------------
    def run(self) -> RunSummary:
        s = self.summary
        batch_id = None
        term_id = None
        if self.loader is not None:
            self.loader.ensure_institution()
            batch_id = self.loader.start_batch(f"{self.adapter.name}:{self.adapter.config.base_url}", self.started_at)
            cy_id = self.loader.ensure_catalog_year(self.term)
            term_id = self.loader.ensure_term(self.term, cy_id)
            self.loader.conn.commit()

        try:
            for raw in self.adapter.fetch_sections(self.term):
                s.seen += 1
                self._process(term_id, raw)
                if self.limit and s.seen >= self.limit:
                    log.info("limit %d reached; stopping (run marked incomplete)", self.limit)
                    break
            else:
                s.complete = True
        except Exception as exc:
            s.error = f"{type(exc).__name__}: {exc}"
            log.exception("term %s aborted after %d sections", self.term.code, s.seen)
        finally:
            try:
                self.adapter.finish(self.term)
            except Exception:
                pass

        if self.loader is not None and term_id is not None:
            self._finish_db(term_id, batch_id)

        s.unmapped_codes = {k: dict(v.most_common()) for k, v in self._unmapped.items()}
        s.requests_made = self.adapter.http.requests_made
        s.retries = self.adapter.http.retries
        s.finished_at = datetime.now(timezone.utc).isoformat()
        return s

    def _process(self, term_id: Optional[int], raw) -> None:
        s = self.summary
        try:
            rec = self.adapter.parse_section(self.term, raw)
        except Exception as exc:  # ParseError or anything the adapter let through
            self._record_failure(raw.source_key, f"parse: {type(exc).__name__}: {exc}", raw.payload)
            return

        for u in rec.unrecognized:
            s.unrecognized_meeting_patterns.append({"crn": u.crn, "reason": u.reason, "raw": u.raw})
            log.warning("crn %s: unrecognized meeting pattern (%s); block skipped", u.crn, u.reason)
        for fld, code in rec.unmapped_codes:
            self._unmapped[fld][code] += 1
        for m in rec.meetings:
            if self._span is None:
                self._span = [m.start_date, m.end_date]
            else:
                self._span[0] = min(self._span[0], m.start_date)
                self._span[1] = max(self._span[1], m.end_date)

        if self.loader is None:
            return
        try:
            with self.loader.conn.transaction():
                self._load(term_id, rec)
        except Exception as exc:
            self.loader.reset_caches()   # ids minted inside the rolled-back transaction are gone
            self._record_failure(rec.crn, f"load: {type(exc).__name__}: {exc}", raw.payload)

    def _load(self, term_id: int, rec: SectionRecord) -> None:
        L, s, now = self.loader, self.summary, self.started_at
        cy_id = L.ensure_catalog_year(self.term)
        course_id = L.ensure_course(cy_id, rec)
        span = None
        if rec.meetings:
            span = (min(m.start_date for m in rec.meetings), max(m.end_date for m in rec.meetings))
        part_id = L.ensure_term_part(term_id, rec.part_of_term_code, span) if rec.part_of_term_code else None
        xl_id = L.ensure_crosslist_group(term_id, rec)
        instructors = [(L.ensure_instructor(i), i.role) for i in rec.instructors]

        out = L.upsert_section(term_id, part_id, course_id, xl_id, rec, now)
        changed_i = L.sync_instructors(out.section_id, instructors, now)
        changed_m = L.sync_meetings(out.section_id, rec.meetings)
        if out.inserted or out.counters_changed:
            L.snapshot(out.section_id, rec, now)

        if out.inserted:
            s.inserted += 1
        elif out.changed or changed_i or changed_m:
            s.updated += 1
        else:
            s.unchanged += 1

    def _record_failure(self, key: str, error: str, payload: Any) -> None:
        self.summary.failed += 1
        self.summary.failures.append({"crn": key, "error": error})
        raw = json.dumps(payload, default=_json_default)
        log.error("crn %s: %s | raw=%s", key, error, raw)
        if self.failures_file is not None:
            self.failures_file.write(json.dumps({"crn": key, "error": error, "raw": payload}, default=_json_default) + "\n")
            self.failures_file.flush()

    def _finish_db(self, term_id: int, batch_id: Optional[int]) -> None:
        L, s = self.loader, self.summary
        try:
            with L.conn.transaction():
                if s.complete:
                    missing = L.unseen_sections(term_id, self.started_at)
                    s.missing = len(missing)
                    if missing and self.mark_missing_cancelled:
                        s.cancelled_marked = L.mark_cancelled(term_id, missing, self.started_at)
                    if self._span:
                        L.update_term_dates(term_id, self._span[0], self._span[1])
                if batch_id is not None:
                    L.finish_batch(batch_id, json.dumps({k: v for k, v in asdict(s).items()
                                                         if k not in ("failures", "unrecognized_meeting_patterns")}))
            L.conn.commit()
        except Exception as exc:
            log.exception("post-run bookkeeping failed")
            s.error = (s.error + "; " if s.error else "") + f"finish: {type(exc).__name__}: {exc}"
