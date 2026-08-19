"""Immutable local evidence storage: content-addressed files plus SQLite metadata."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clock import format_timestamp, parse_timestamp
from .models import (
    EvidenceRecord,
    LLMCallRecord,
    ScenarioDefinition,
    ScenarioSnapshot,
    SearchManifest,
    SearchTraceRecord,
    TemporalSearchResponse,
    TemporalSearchResult,
    ToolTraceRecord,
    canonical_json,
    request_key,
)


class TemporalStore:
    """A local evidence store designed to promote cleanly to object storage and Postgres."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.artifacts_dir = self.root / "artifacts"
        self.database_path = self.root / "temporal.sqlite3"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS artifacts (
                    content_hash TEXT PRIMARY KEY,
                    media_type TEXT NOT NULL,
                    byte_length INTEGER NOT NULL,
                    path TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    tool TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL REFERENCES artifacts(content_hash),
                    event_at TEXT,
                    source_published_at TEXT,
                    available_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    fidelity TEXT NOT NULL,
                    source TEXT,
                    is_error INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS evidence_lookup
                ON evidence(tool, request_key, available_at, observed_at, evidence_id);
                CREATE TABLE IF NOT EXISTS llm_calls (
                    call_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    request_artifact_hash TEXT NOT NULL REFERENCES artifacts(content_hash),
                    response_json TEXT,
                    response_artifact_hash TEXT REFERENCES artifacts(content_hash),
                    error_json TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    scenario_id TEXT,
                    mode TEXT,
                    temporal_run_id TEXT,
                    temporal_sequence INTEGER
                );
                CREATE INDEX IF NOT EXISTS llm_calls_scenario
                ON llm_calls(scenario_id, started_at, call_id);
                CREATE TABLE IF NOT EXISTS scenario_snapshots (
                    scenario_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL REFERENCES artifacts(content_hash),
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (scenario_id, name)
                );
                CREATE TABLE IF NOT EXISTS scenarios (
                    scenario_id TEXT PRIMARY KEY,
                    as_of TEXT NOT NULL,
                    basis TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    corpus_hash TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL REFERENCES artifacts(content_hash),
                    created_at TEXT NOT NULL,
                    capture_run_id TEXT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
                    evidence_id UNINDEXED,
                    content
                );
                CREATE TABLE IF NOT EXISTS tool_traces (
                    trace_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    scenario_id TEXT,
                    mode TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    evidence_id TEXT REFERENCES evidence(evidence_id),
                    invoked_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS tool_traces_run
                ON tool_traces(run_id, sequence);
                CREATE TABLE IF NOT EXISTS search_traces (
                    trace_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    scenario_id TEXT,
                    mode TEXT NOT NULL,
                    query TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    ranker_version TEXT NOT NULL,
                    corpus_hash TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    invoked_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS search_traces_run
                ON search_traces(run_id, sequence);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(evidence)")}
            if "is_error" not in columns:
                connection.execute(
                    "ALTER TABLE evidence ADD COLUMN is_error INTEGER NOT NULL DEFAULT 0"
                )
            if "event_at" not in columns:
                connection.execute("ALTER TABLE evidence ADD COLUMN event_at TEXT")
            if "source_published_at" not in columns:
                connection.execute("ALTER TABLE evidence ADD COLUMN source_published_at TEXT")
            scenario_columns = {row["name"] for row in connection.execute("PRAGMA table_info(scenarios)")}
            if "corpus_hash" not in scenario_columns:
                connection.execute("ALTER TABLE scenarios ADD COLUMN corpus_hash TEXT NOT NULL DEFAULT ''")
            if "capture_run_id" not in scenario_columns:
                connection.execute("ALTER TABLE scenarios ADD COLUMN capture_run_id TEXT")
            llm_columns = {row["name"] for row in connection.execute("PRAGMA table_info(llm_calls)")}
            if "temporal_run_id" not in llm_columns:
                connection.execute("ALTER TABLE llm_calls ADD COLUMN temporal_run_id TEXT")
            if "temporal_sequence" not in llm_columns:
                connection.execute("ALTER TABLE llm_calls ADD COLUMN temporal_sequence INTEGER")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS llm_calls_temporal_run "
                "ON llm_calls(temporal_run_id, temporal_sequence, call_id)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS llm_calls_temporal_sequence "
                "ON llm_calls(temporal_run_id, temporal_sequence) "
                "WHERE temporal_run_id IS NOT NULL"
            )
            connection.execute("DELETE FROM evidence_fts")
            connection.execute(
                """
                INSERT INTO evidence_fts(evidence_id, content)
                SELECT evidence_id, response_json FROM evidence
                WHERE tool = 'corpus.document' AND is_error = 0
                """
            )

    def put_artifact(self, content: bytes, media_type: str = "application/json") -> str:
        """Persist content once and return its SHA-256 address."""
        content_hash = hashlib.sha256(content).hexdigest()
        relative_path = Path(content_hash[:2]) / content_hash[2:]
        artifact_path = self.artifacts_dir / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if not artifact_path.exists():
            artifact_path.write_bytes(content)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO artifacts(content_hash, media_type, byte_length, path)
                VALUES (?, ?, ?, ?)
                """,
                (content_hash, media_type, len(content), str(relative_path)),
            )
        return content_hash

    def read_artifact(self, content_hash: str) -> bytes:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path FROM artifacts WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown artifact: {content_hash}")
        return (self.artifacts_dir / row["path"]).read_bytes()

    def record(
        self,
        tool: str,
        request: Mapping[str, Any],
        response: Any,
        *,
        available_at: datetime,
        observed_at: datetime | None = None,
        event_at: datetime | None = None,
        source_published_at: datetime | None = None,
        fidelity: str = "forward-captured",
        source: str | None = None,
        is_error: bool = False,
    ) -> EvidenceRecord:
        """Capture a JSON-compatible response as immutable evidence."""
        available = parse_timestamp(available_at)
        observed = parse_timestamp(observed_at or available)
        event = parse_timestamp(event_at) if event_at is not None else None
        source_published = (
            parse_timestamp(source_published_at) if source_published_at is not None else None
        )
        ingested = datetime.now(timezone.utc)
        normalized_request = json.loads(canonical_json(dict(request)))
        response_json = canonical_json(response)
        artifact_hash = self.put_artifact(response_json.encode("utf-8"))
        key = request_key(tool, normalized_request)
        identity = {
            "tool": tool,
            "request_key": key,
            "artifact_hash": artifact_hash,
            "event_at": format_timestamp(event) if event is not None else None,
            "source_published_at": (
                format_timestamp(source_published) if source_published is not None else None
            ),
            "available_at": format_timestamp(available),
            "observed_at": format_timestamp(observed),
            "fidelity": fidelity,
            "source": source,
            "is_error": is_error,
        }
        evidence_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        record = EvidenceRecord(
            evidence_id=evidence_id,
            tool=tool,
            request_key=key,
            request=normalized_request,
            response=json.loads(response_json),
            artifact_hash=artifact_hash,
            available_at=available,
            observed_at=observed,
            ingested_at=ingested,
            fidelity=fidelity,
            event_at=event,
            source_published_at=source_published,
            source=source,
            is_error=is_error,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence(
                    evidence_id, tool, request_key, request_json, response_json, artifact_hash,
                    event_at, source_published_at, available_at, observed_at, ingested_at,
                    fidelity, source, is_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.evidence_id,
                    record.tool,
                    record.request_key,
                    canonical_json(record.request),
                    response_json,
                    record.artifact_hash,
                    format_timestamp(record.event_at) if record.event_at is not None else None,
                    (
                        format_timestamp(record.source_published_at)
                        if record.source_published_at is not None
                        else None
                    ),
                    format_timestamp(record.available_at),
                    format_timestamp(record.observed_at),
                    format_timestamp(record.ingested_at),
                    record.fidelity,
                    record.source,
                    int(record.is_error),
                ),
            )
            connection.execute("DELETE FROM evidence_fts WHERE evidence_id = ?", (record.evidence_id,))
            if record.tool == "corpus.document" and not record.is_error:
                connection.execute(
                    "INSERT INTO evidence_fts(evidence_id, content) VALUES (?, ?)",
                    (record.evidence_id, response_json),
                )
        return record

    def record_error(
        self,
        tool: str,
        request: Mapping[str, Any],
        error: Exception,
        *,
        available_at: datetime,
        observed_at: datetime | None = None,
        event_at: datetime | None = None,
        source_published_at: datetime | None = None,
        fidelity: str = "forward-captured",
        source: str | None = None,
    ) -> EvidenceRecord:
        """Persist a tool failure so replay preserves the same unavailable world."""
        return self.record(
            tool,
            request,
            {"error_type": type(error).__name__, "message": str(error)},
            available_at=available_at,
            observed_at=observed_at,
            event_at=event_at,
            source_published_at=source_published_at,
            fidelity=fidelity,
            source=source,
            is_error=True,
        )

    def latest_eligible(
        self,
        tool: str,
        request: Mapping[str, Any],
        *,
        as_of: datetime,
    ) -> EvidenceRecord | None:
        """Return the most recently available evidence for exactly one request."""
        cutoff = format_timestamp(parse_timestamp(as_of))
        key = request_key(tool, request)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM evidence
                WHERE tool = ? AND request_key = ? AND available_at <= ?
                ORDER BY available_at DESC, observed_at DESC, evidence_id DESC
                LIMIT 1
                """,
                (tool, key, cutoff),
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def get_evidence(self, evidence_id: str) -> EvidenceRecord:
        """Return one immutable evidence record by its content-addressed identity."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown evidence: {evidence_id}")
        return self._record_from_row(row)

    def corpus_hash(self, *, as_of: datetime) -> str:
        """Digest the exact evidence corpus eligible at one virtual time."""
        cutoff = format_timestamp(parse_timestamp(as_of))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT evidence_id FROM evidence WHERE available_at <= ? ORDER BY evidence_id",
                (cutoff,),
            ).fetchall()
        payload = {"as_of": cutoff, "evidence_ids": [row["evidence_id"] for row in rows]}
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def seal_scenario_snapshot(
        self,
        scenario_id: str,
        name: str,
        state: Any,
        *,
        captured_at: datetime,
    ) -> ScenarioSnapshot:
        """Seal one named scenario input; later conflicting writes are rejected."""
        if not scenario_id or not name:
            raise ValueError("scenario_id and name are required")
        captured = parse_timestamp(captured_at)
        state_json = canonical_json(state)
        artifact_hash = self.put_artifact(state_json.encode("utf-8"))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM scenario_snapshots WHERE scenario_id = ? AND name = ?",
                (scenario_id, name),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO scenario_snapshots(
                        scenario_id, name, state_json, artifact_hash, captured_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (scenario_id, name, state_json, artifact_hash, format_timestamp(captured)),
                )
                return ScenarioSnapshot(scenario_id, name, json.loads(state_json), artifact_hash, captured)
            if existing["state_json"] != state_json:
                raise ValueError(f"scenario snapshot already sealed: {scenario_id}/{name}")
        return self._snapshot_from_row(existing)

    def seal_scenario(
        self,
        scenario_id: str,
        *,
        as_of: datetime,
        basis: str,
        metadata: Mapping[str, Any] | None = None,
        corpus_hash: str | None = None,
        created_at: datetime | None = None,
        capture_run_id: str | None = None,
    ) -> ScenarioDefinition:
        """Seal the identity of one reproducible historical environment."""
        if not scenario_id or not basis:
            raise ValueError("scenario_id and basis are required")
        definition_time = parse_timestamp(as_of)
        created = parse_timestamp(created_at or datetime.now(timezone.utc))
        metadata_json = canonical_json(dict(metadata or {}))
        sealed_corpus_hash = corpus_hash or self.corpus_hash(as_of=definition_time)
        artifact_hash = self.put_artifact(metadata_json.encode("utf-8"))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO scenarios(
                        scenario_id, as_of, basis, metadata_json, corpus_hash, artifact_hash, created_at,
                        capture_run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scenario_id,
                        format_timestamp(definition_time),
                        basis,
                        metadata_json,
                        sealed_corpus_hash,
                        artifact_hash,
                        format_timestamp(created),
                        capture_run_id,
                    ),
                )
                return ScenarioDefinition(
                    scenario_id,
                    definition_time,
                    basis,
                    json.loads(metadata_json),
                    sealed_corpus_hash,
                    artifact_hash,
                    created,
                    capture_run_id,
                )
            if (
                existing["as_of"] != format_timestamp(definition_time)
                or existing["basis"] != basis
                or existing["metadata_json"] != metadata_json
                or existing["corpus_hash"] != sealed_corpus_hash
                or existing["capture_run_id"] != capture_run_id
            ):
                raise ValueError(f"scenario already sealed: {scenario_id}")
        return self._scenario_from_row(existing)

    def get_scenario(self, scenario_id: str) -> ScenarioDefinition | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)
            ).fetchone()
        return self._scenario_from_row(row) if row is not None else None

    def verify_scenario_corpus(self, scenario_id: str) -> bool:
        """Return whether the current eligible corpus still matches a sealed scenario."""
        scenario = self.get_scenario(scenario_id)
        if scenario is None:
            raise KeyError(f"unknown scenario: {scenario_id}")
        return scenario.corpus_hash == self.corpus_hash(as_of=scenario.as_of)

    def get_scenario_snapshot(self, scenario_id: str, name: str) -> ScenarioSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scenario_snapshots WHERE scenario_id = ? AND name = ?",
                (scenario_id, name),
            ).fetchone()
        return self._snapshot_from_row(row) if row is not None else None

    def begin_llm_call(
        self,
        run_id: str,
        request: Mapping[str, Any],
        *,
        started_at: datetime | None = None,
        scenario_id: str | None = None,
        mode: str | None = None,
        temporal_run_id: str | None = None,
    ) -> LLMCallRecord:
        """Persist an LLM request before a provider response is available."""
        started = parse_timestamp(started_at or datetime.now(timezone.utc))
        request_json = canonical_json(dict(request))
        request_hash = self.put_artifact(request_json.encode("utf-8"))
        call_id = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            # A LangGraph run can issue parallel model calls. Reserve the
            # tape sequence under SQLite's write lock so no call is lost to a
            # duplicate sequence race.
            connection.execute("BEGIN IMMEDIATE")
            temporal_sequence = None
            if temporal_run_id is not None:
                temporal_sequence = connection.execute(
                    "SELECT COALESCE(MAX(temporal_sequence), 0) + 1 "
                    "FROM llm_calls WHERE temporal_run_id = ?",
                    (temporal_run_id,),
                ).fetchone()[0]
            connection.execute(
                """
                INSERT OR IGNORE INTO llm_calls(
                    call_id, run_id, request_json, request_artifact_hash, started_at, scenario_id, mode,
                    temporal_run_id, temporal_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    run_id,
                    request_json,
                    request_hash,
                    format_timestamp(started),
                    scenario_id,
                    mode,
                    temporal_run_id,
                    temporal_sequence,
                ),
            )
        return self.get_llm_call(run_id)

    def finish_llm_call(
        self,
        run_id: str,
        *,
        response: Any | None = None,
        error: Mapping[str, str] | None = None,
        completed_at: datetime | None = None,
    ) -> LLMCallRecord:
        """Seal an LLM call with either a response or a captured provider error."""
        if (response is None) == (error is None):
            raise ValueError("provide exactly one of response or error")
        completed = parse_timestamp(completed_at or datetime.now(timezone.utc))
        response_json = canonical_json(response) if response is not None else None
        response_hash = self.put_artifact(response_json.encode("utf-8")) if response_json else None
        error_json = canonical_json(dict(error)) if error is not None else None
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE llm_calls
                SET response_json = ?, response_artifact_hash = ?, error_json = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (response_json, response_hash, error_json, format_timestamp(completed), run_id),
            ).rowcount
        if not updated:
            raise KeyError(f"unknown LLM run: {run_id}")
        return self.get_llm_call(run_id)

    def get_llm_call(self, run_id: str) -> LLMCallRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM llm_calls WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown LLM run: {run_id}")
        return self._llm_call_from_row(row)

    def list_llm_calls(
        self,
        scenario_id: str | None = None,
        *,
        temporal_run_id: str | None = None,
    ) -> list[LLMCallRecord]:
        query = "SELECT * FROM llm_calls"
        conditions: list[str] = []
        parameters: list[Any] = []
        if scenario_id is not None:
            conditions.append("scenario_id = ?")
            parameters.append(scenario_id)
        if temporal_run_id is not None:
            conditions.append("temporal_run_id = ?")
            parameters.append(temporal_run_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += (
            " ORDER BY temporal_sequence, call_id"
            if temporal_run_id is not None
            else " ORDER BY started_at, call_id"
        )
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._llm_call_from_row(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        as_of: datetime,
        limit: int = 10,
    ) -> TemporalSearchResponse:
        """Search only evidence that was available at the virtual-time boundary."""
        if limit < 1:
            raise ValueError("limit must be positive")
        fts_query = _fts_query(query)
        cutoff = format_timestamp(parse_timestamp(as_of))
        if not fts_query:
            return TemporalSearchResponse(
                results=(),
                manifest=SearchManifest(
                    query=query,
                    as_of=parse_timestamp(as_of),
                    ranker_version="sqlite-fts5-v1",
                    corpus_hash=self.corpus_hash(as_of=as_of),
                    evidence_ids=(),
                ),
            )
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence.*, bm25(evidence_fts) AS rank
                FROM evidence_fts
                JOIN evidence ON evidence.evidence_id = evidence_fts.evidence_id
                WHERE evidence_fts MATCH ? AND evidence.available_at <= ?
                ORDER BY rank ASC, evidence.evidence_id ASC
                LIMIT ?
                """,
                (fts_query, cutoff, limit),
            ).fetchall()
        results = tuple(
            TemporalSearchResult(evidence=self._record_from_row(row), rank=float(row["rank"]))
            for row in rows
        )
        return TemporalSearchResponse(
            results=results,
            manifest=SearchManifest(
                query=query,
                as_of=parse_timestamp(as_of),
                ranker_version="sqlite-fts5-v1",
                corpus_hash=self.corpus_hash(as_of=as_of),
                evidence_ids=tuple(result.evidence.evidence_id for result in results),
            ),
        )

    def record_tool_trace(
        self,
        *,
        run_id: str,
        scenario_id: str | None,
        mode: str,
        tool: str,
        request: Mapping[str, Any],
        evidence_id: str | None,
        invoked_at: datetime | None = None,
    ) -> ToolTraceRecord:
        """Append one selected tool result to a temporal run trace."""
        invoked = parse_timestamp(invoked_at or datetime.now(timezone.utc))
        key = request_key(tool, request)
        with self._connect() as connection:
            # Tool nodes may also run concurrently; sequence allocation is an
            # ordered tape operation rather than a best-effort counter.
            connection.execute("BEGIN IMMEDIATE")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM tool_traces WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            identity = {
                "run_id": run_id,
                "sequence": sequence,
                "tool": tool,
                "request_key": key,
                "evidence_id": evidence_id,
            }
            trace_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO tool_traces(
                    trace_id, run_id, sequence, scenario_id, mode, tool, request_key, evidence_id, invoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    run_id,
                    sequence,
                    scenario_id,
                    mode,
                    tool,
                    key,
                    evidence_id,
                    format_timestamp(invoked),
                ),
            )
        return ToolTraceRecord(
            trace_id=trace_id,
            run_id=run_id,
            sequence=sequence,
            scenario_id=scenario_id,
            mode=mode,
            tool=tool,
            request_key=key,
            evidence_id=evidence_id,
            invoked_at=invoked,
        )

    def record_search_trace(
        self,
        *,
        run_id: str,
        scenario_id: str | None,
        mode: str,
        manifest: SearchManifest,
        invoked_at: datetime | None = None,
    ) -> SearchTraceRecord:
        """Append the exact result manifest returned by an owned search call."""
        invoked = parse_timestamp(invoked_at or datetime.now(timezone.utc))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM search_traces WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            identity = {
                "run_id": run_id,
                "sequence": sequence,
                "query": manifest.query,
                "as_of": format_timestamp(manifest.as_of),
                "corpus_hash": manifest.corpus_hash,
                "evidence_ids": manifest.evidence_ids,
            }
            trace_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO search_traces(
                    trace_id, run_id, sequence, scenario_id, mode, query, as_of, ranker_version,
                    corpus_hash, evidence_ids_json, invoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    run_id,
                    sequence,
                    scenario_id,
                    mode,
                    manifest.query,
                    format_timestamp(manifest.as_of),
                    manifest.ranker_version,
                    manifest.corpus_hash,
                    canonical_json(manifest.evidence_ids),
                    format_timestamp(invoked),
                ),
            )
        return SearchTraceRecord(
            trace_id,
            run_id,
            sequence,
            scenario_id,
            mode,
            manifest,
            invoked,
        )

    def list_tool_traces(self, run_id: str) -> list[ToolTraceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_traces WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [self._tool_trace_from_row(row) for row in rows]

    def get_tool_trace(self, run_id: str, sequence: int) -> ToolTraceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_traces WHERE run_id = ? AND sequence = ?",
                (run_id, sequence),
            ).fetchone()
        return self._tool_trace_from_row(row) if row is not None else None

    def list_search_traces(self, run_id: str) -> list[SearchTraceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM search_traces WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [self._search_trace_from_row(row) for row in rows]

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            tool=row["tool"],
            request_key=row["request_key"],
            request=json.loads(row["request_json"]),
            response=json.loads(row["response_json"]),
            artifact_hash=row["artifact_hash"],
            available_at=parse_timestamp(row["available_at"]),
            observed_at=parse_timestamp(row["observed_at"]),
            ingested_at=parse_timestamp(row["ingested_at"]),
            fidelity=row["fidelity"],
            event_at=parse_timestamp(row["event_at"]) if row["event_at"] else None,
            source_published_at=(
                parse_timestamp(row["source_published_at"])
                if row["source_published_at"]
                else None
            ),
            source=row["source"],
            is_error=bool(row["is_error"]),
        )

    @staticmethod
    def _llm_call_from_row(row: sqlite3.Row) -> LLMCallRecord:
        return LLMCallRecord(
            call_id=row["call_id"],
            run_id=row["run_id"],
            request=json.loads(row["request_json"]),
            response=json.loads(row["response_json"]) if row["response_json"] else None,
            error=json.loads(row["error_json"]) if row["error_json"] else None,
            started_at=parse_timestamp(row["started_at"]),
            completed_at=parse_timestamp(row["completed_at"]) if row["completed_at"] else None,
            scenario_id=row["scenario_id"],
            mode=row["mode"],
            temporal_run_id=row["temporal_run_id"],
            temporal_sequence=row["temporal_sequence"],
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> ScenarioSnapshot:
        return ScenarioSnapshot(
            scenario_id=row["scenario_id"],
            name=row["name"],
            state=json.loads(row["state_json"]),
            artifact_hash=row["artifact_hash"],
            captured_at=parse_timestamp(row["captured_at"]),
        )

    @staticmethod
    def _scenario_from_row(row: sqlite3.Row) -> ScenarioDefinition:
        return ScenarioDefinition(
            scenario_id=row["scenario_id"],
            as_of=parse_timestamp(row["as_of"]),
            basis=row["basis"],
            metadata=json.loads(row["metadata_json"]),
            corpus_hash=row["corpus_hash"],
            artifact_hash=row["artifact_hash"],
            created_at=parse_timestamp(row["created_at"]),
            capture_run_id=row["capture_run_id"],
        )

    @staticmethod
    def _tool_trace_from_row(row: sqlite3.Row) -> ToolTraceRecord:
        return ToolTraceRecord(
            trace_id=row["trace_id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            scenario_id=row["scenario_id"],
            mode=row["mode"],
            tool=row["tool"],
            request_key=row["request_key"],
            evidence_id=row["evidence_id"],
            invoked_at=parse_timestamp(row["invoked_at"]),
        )

    @staticmethod
    def _search_trace_from_row(row: sqlite3.Row) -> SearchTraceRecord:
        manifest = SearchManifest(
            query=row["query"],
            as_of=parse_timestamp(row["as_of"]),
            ranker_version=row["ranker_version"],
            corpus_hash=row["corpus_hash"],
            evidence_ids=tuple(json.loads(row["evidence_ids_json"])),
        )
        return SearchTraceRecord(
            trace_id=row["trace_id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            scenario_id=row["scenario_id"],
            mode=row["mode"],
            manifest=manifest,
            invoked_at=parse_timestamp(row["invoked_at"]),
        )


def _fts_query(query: str) -> str:
    """Turn user text into a conservative FTS5 query without FTS syntax injection."""
    return " AND ".join(f'"{token}"' for token in re.findall(r"[\w]+", query, flags=re.UNICODE))
