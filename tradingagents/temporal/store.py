"""Immutable local evidence storage: content-addressed files plus SQLite metadata."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clock import format_timestamp, parse_timestamp
from .documents import chunks, extract_document, stable_chunk_id, stable_doc_key, title_similarity
from .models import (
    EvidenceRecord,
    LLMCallRecord,
    ResearchRunRecord,
    ScenarioDefinition,
    ScenarioRubricRecord,
    ScenarioSnapshot,
    SearchManifest,
    SearchTraceRecord,
    TemporalDocument,
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
                CREATE TABLE IF NOT EXISTS scenario_rubrics (
                    scenario_id TEXT PRIMARY KEY REFERENCES scenarios(scenario_id),
                    material_evidence_ids_json TEXT NOT NULL,
                    useful_evidence_ids_json TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL REFERENCES artifacts(content_hash),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    decision TEXT,
                    report_artifact_hash TEXT REFERENCES artifacts(content_hash),
                    completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_runs_scenario
                ON research_runs(scenario_id, completed_at, run_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
                    evidence_id UNINDEXED,
                    content
                );
                CREATE TABLE IF NOT EXISTS documents (
                    doc_key TEXT PRIMARY KEY,
                    parent_evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
                    logical_position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    source_domain TEXT NOT NULL,
                    canonical_url TEXT,
                    published_at TEXT,
                    available_at TEXT NOT NULL,
                    doc_kind TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    cluster_key TEXT NOT NULL,
                    UNIQUE(parent_evidence_id, logical_position)
                );
                CREATE INDEX IF NOT EXISTS documents_available ON documents(available_at, doc_key);
                CREATE TABLE IF NOT EXISTS document_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_key TEXT NOT NULL REFERENCES documents(doc_key),
                    chunk_index INTEGER NOT NULL,
                    token_start INTEGER NOT NULL,
                    token_end INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    UNIQUE(doc_key, chunk_index)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    body, content='document_chunks', content_rowid='rowid'
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
            rubric_columns = {row["name"] for row in connection.execute("PRAGMA table_info(scenario_rubrics)")}
            if "material_document_keys_json" not in rubric_columns:
                connection.execute("ALTER TABLE scenario_rubrics ADD COLUMN material_document_keys_json TEXT NOT NULL DEFAULT '[]'")
            if "useful_document_keys_json" not in rubric_columns:
                connection.execute("ALTER TABLE scenario_rubrics ADD COLUMN useful_document_keys_json TEXT NOT NULL DEFAULT '[]'")
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
                self._maintain_document(connection, record)
                self._refresh_document_clusters(connection)
        return record

    @staticmethod
    def _maintain_document(connection: sqlite3.Connection, record: EvidenceRecord) -> None:
        extracted = extract_document({
            "tool": record.tool, "request": record.request, "response": record.response,
            "source": record.source, "available_at": format_timestamp(record.available_at),
            "event_at": format_timestamp(record.event_at) if record.event_at else None,
            "source_published_at": format_timestamp(record.source_published_at) if record.source_published_at else None,
            "is_error": record.is_error,
        })
        if extracted is None:
            return
        doc_key = stable_doc_key(record.evidence_id, 0)
        connection.execute("""INSERT OR IGNORE INTO documents
            (doc_key,parent_evidence_id,logical_position,title,body,source_domain,canonical_url,published_at,
             available_at,doc_kind,extractor_version,cluster_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            doc_key, record.evidence_id, 0, extracted["title"], extracted["body"], extracted["source_domain"],
            extracted["canonical_url"], extracted["published_at"], extracted["available_at"], extracted["doc_kind"],
            extracted["extractor_version"], doc_key))
        for index, (start, end, body) in enumerate(chunks(extracted["body"])):
            chunk_id = stable_chunk_id(doc_key, index)
            inserted = connection.execute("""INSERT OR IGNORE INTO document_chunks
                (chunk_id,doc_key,chunk_index,token_start,token_end,body) VALUES (?,?,?,?,?,?)""",
                (chunk_id, doc_key, index, start, end, body)).rowcount
            if inserted:
                rowid = connection.execute("SELECT rowid FROM document_chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()[0]
                connection.execute("INSERT INTO document_chunks_fts(rowid, body) VALUES (?, ?)", (rowid, body))

    def reindex_documents(self) -> int:
        """Idempotently rebuild only the derivative document layer."""
        with self._connect() as connection:
            connection.execute("DELETE FROM document_chunks_fts")
            connection.execute("DELETE FROM document_chunks")
            connection.execute("DELETE FROM documents")
            for row in connection.execute("SELECT * FROM evidence WHERE tool='corpus.document' AND is_error=0 ORDER BY evidence_id"):
                self._maintain_document(connection, self._record_from_row(row))
            self._refresh_document_clusters(connection)
            self._migrate_rubric_document_keys(connection)
            return connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    @staticmethod
    def _migrate_rubric_document_keys(connection: sqlite3.Connection) -> None:
        """Backfill document-key labels for R1 rubrics during an explicit reindex."""
        rows = connection.execute("SELECT * FROM scenario_rubrics").fetchall()
        for row in rows:
            material = tuple(json.loads(row["material_evidence_ids_json"]))
            useful = tuple(json.loads(row["useful_evidence_ids_json"]))
            mapping = {
                r["parent_evidence_id"]: r["doc_key"]
                for r in connection.execute(
                    "SELECT parent_evidence_id, doc_key FROM documents WHERE parent_evidence_id IN ({})".format(",".join("?" for _ in useful)),
                    useful,
                ).fetchall()
            } if useful else {}
            if set(mapping) == set(useful):
                connection.execute(
                    "UPDATE scenario_rubrics SET material_document_keys_json=?, useful_document_keys_json=? WHERE scenario_id=?",
                    (canonical_json(tuple(mapping[key] for key in material)), canonical_json(tuple(mapping[key] for key in useful)), row["scenario_id"]),
                )

    @staticmethod
    def _refresh_document_clusters(connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT doc_key, title, canonical_url, source_domain FROM documents ORDER BY doc_key").fetchall()
        clusters: dict[str, str] = {}
        for row in rows:
            cluster = row["doc_key"]
            for other in rows:
                if other["doc_key"] == row["doc_key"]:
                    continue
                same_url = row["canonical_url"] and row["canonical_url"] == other["canonical_url"]
                similar = row["source_domain"] == other["source_domain"] and title_similarity(row["title"], other["title"]) >= 0.92
                if same_url or similar:
                    cluster = min(cluster, clusters.get(other["doc_key"], other["doc_key"]))
            clusters[row["doc_key"]] = cluster
        connection.executemany("UPDATE documents SET cluster_key=? WHERE doc_key=?", [(cluster, key) for key, cluster in clusters.items()])

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

    def seal_scenario_rubric(
        self,
        scenario_id: str,
        *,
        material_evidence_ids: Iterable[str],
        useful_evidence_ids: Iterable[str],
        created_at: datetime | None = None,
    ) -> ScenarioRubricRecord:
        """Seal the relevance labels used to score every arm of one scenario."""
        material = _normalized_evidence_ids(material_evidence_ids, "material_evidence_ids")
        useful = _normalized_evidence_ids(useful_evidence_ids, "useful_evidence_ids")
        if not material:
            raise ValueError("material_evidence_ids must not be empty")
        if not set(material) <= set(useful):
            raise ValueError("useful_evidence_ids must include all material evidence")
        created = parse_timestamp(created_at or datetime.now(timezone.utc))
        payload = {
            "scenario_id": scenario_id,
            "material_evidence_ids": material,
            "useful_evidence_ids": useful,
        }
        artifact_hash = self.put_artifact(canonical_json(payload).encode("utf-8"))
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM scenarios WHERE scenario_id = ?", (scenario_id,)
            ).fetchone() is None:
                raise KeyError(f"unknown scenario: {scenario_id}")
            known = {
                row["evidence_id"]
                for row in connection.execute(
                    f"SELECT evidence_id FROM evidence WHERE evidence_id IN ({','.join('?' for _ in useful)})",
                    useful,
                ).fetchall()
            }
            missing = sorted(set(useful) - known)
            if missing:
                raise KeyError(f"unknown rubric evidence: {missing[0]}")
            document_keys = {
                row["evidence_id"]: row["doc_key"]
                for row in connection.execute(
                    f"SELECT parent_evidence_id AS evidence_id, doc_key FROM documents WHERE parent_evidence_id IN ({','.join('?' for _ in useful)})",
                    useful,
                ).fetchall()
            }
            # Rubrics mix searchable documents with tool-tape evidence (price
            # data, fundamentals). Only corpus documents must map to stable
            # document keys; tool-tape evidence stays evidence-id-labeled.
            document_backed = {
                row["evidence_id"]
                for row in connection.execute(
                    f"SELECT evidence_id FROM evidence WHERE tool = 'corpus.document' AND evidence_id IN ({','.join('?' for _ in useful)})",
                    useful,
                ).fetchall()
            }
            if document_backed - set(document_keys):
                raise ValueError("document layer must be reindexed before sealing a rubric")
            material_documents = tuple(document_keys[key] for key in material if key in document_keys)
            useful_documents = tuple(document_keys[key] for key in useful if key in document_keys)
            existing = connection.execute(
                "SELECT * FROM scenario_rubrics WHERE scenario_id = ?", (scenario_id,)
            ).fetchone()
            material_json = canonical_json(material)
            useful_json = canonical_json(useful)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO scenario_rubrics(
                        scenario_id, material_evidence_ids_json, useful_evidence_ids_json,
                        material_document_keys_json, useful_document_keys_json, artifact_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (scenario_id, material_json, useful_json, canonical_json(material_documents), canonical_json(useful_documents), artifact_hash, format_timestamp(created)),
                )
                return ScenarioRubricRecord(scenario_id, material, useful, artifact_hash, created)
            if (
                existing["material_evidence_ids_json"] != material_json
                or existing["useful_evidence_ids_json"] != useful_json
                or existing["material_document_keys_json"] != canonical_json(material_documents)
                or existing["useful_document_keys_json"] != canonical_json(useful_documents)
            ):
                raise ValueError(f"scenario rubric already sealed: {scenario_id}")
        return self._rubric_from_row(existing)

    def get_scenario_rubric(self, scenario_id: str) -> ScenarioRubricRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scenario_rubrics WHERE scenario_id = ?", (scenario_id,)
            ).fetchone()
        return self._rubric_from_row(row) if row is not None else None

    def record_research_run(
        self,
        run_id: str,
        scenario_id: str,
        *,
        decision: str | None,
        report: str | None = None,
        completed_at: datetime | None = None,
    ) -> ResearchRunRecord:
        """Seal the final output of an evidence-replay run exactly once."""
        if not run_id or not scenario_id:
            raise ValueError("run_id and scenario_id are required")
        if decision is not None and not isinstance(decision, str):
            raise TypeError("decision must be a string or None")
        if report is not None and not isinstance(report, str):
            raise TypeError("report must be a string or None")
        completed = parse_timestamp(completed_at or datetime.now(timezone.utc))
        report_artifact_hash = (
            self.put_artifact(report.encode("utf-8"), media_type="text/markdown")
            if report is not None
            else None
        )
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM research_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO research_runs(
                        run_id, scenario_id, decision, report_artifact_hash, completed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        scenario_id,
                        decision,
                        report_artifact_hash,
                        format_timestamp(completed),
                    ),
                )
                return ResearchRunRecord(
                    run_id, scenario_id, decision, report_artifact_hash, completed
                )
            if (
                existing["scenario_id"] != scenario_id
                or existing["decision"] != decision
                or existing["report_artifact_hash"] != report_artifact_hash
            ):
                raise ValueError(f"research run already sealed: {run_id}")
        return self._research_run_from_row(existing)

    def get_research_run(self, run_id: str) -> ResearchRunRecord | None:
        """Return the persisted final output for one agent run, if it completed."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._research_run_from_row(row) if row is not None else None

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
        page: int = 1,
        date_from: str | None = None,
        date_to: str | None = None,
        source: str | None = None,
        corpus_hash_pin: str | None = None,
    ) -> TemporalSearchResponse:
        """Search eligible normalized chunks and aggregate to one result per document cluster."""
        if limit < 1:
            raise ValueError("limit must be positive")
        if page < 1:
            raise ValueError("page must be positive")
        parsed_as_of = parse_timestamp(as_of)
        start = (date_from[:10] if isinstance(date_from, str) and len(date_from) == 10 else parse_timestamp(date_from).date().isoformat()) if date_from else None
        end = (date_to[:10] if isinstance(date_to, str) and len(date_to) == 10 else parse_timestamp(date_to).date().isoformat()) if date_to else None
        if start and end and start > end:
            raise ValueError("date_from must be on or before date_to")
        current_hash = self.corpus_hash(as_of=parsed_as_of)
        if page > 1 and corpus_hash_pin != current_hash:
            raise ValueError("page > 1 requires a matching page-1 corpus_hash pin")
        fts_query = _fts_query(query)
        cutoff = format_timestamp(parsed_as_of)
        predicates = ["d.available_at <= ?"]
        parameters: list[Any] = [cutoff]
        if start:
            predicates.append("substr(d.available_at, 1, 10) >= ?")
            parameters.append(start)
        if end:
            predicates.append("substr(d.available_at, 1, 10) <= ?")
            parameters.append(end)
        if source:
            predicates.append("(d.source_domain = ? OR e.source = ? OR d.source_domain LIKE ?)")
            parameters.extend((source, source, f"%{source}%"))
        where = " AND ".join(predicates)
        candidate_limit = max(limit * 8 * page, 32)
        with self._connect() as connection:
            if fts_query:
                candidates = connection.execute(
                    f"""
                -- FTS5's rank column is bm25(document_chunks_fts); unlike the
                -- auxiliary function it is legal in a grouped aggregate.
                SELECT c.doc_key, d.cluster_key, MIN(document_chunks_fts.rank) AS rank
                FROM document_chunks_fts
                JOIN document_chunks c ON c.rowid = document_chunks_fts.rowid
                JOIN documents d ON d.doc_key = c.doc_key
                JOIN evidence e ON e.evidence_id = d.parent_evidence_id
                WHERE document_chunks_fts MATCH ? AND {where}
                GROUP BY c.doc_key, d.cluster_key
                ORDER BY rank ASC, c.doc_key ASC
                LIMIT ?
                """, (fts_query, *parameters, candidate_limit)).fetchall()
            else:
                candidates = connection.execute(
                    f"""SELECT d.doc_key, d.cluster_key, 0.0 AS rank
                    FROM documents d JOIN evidence e ON e.evidence_id=d.parent_evidence_id
                    WHERE {where} ORDER BY d.available_at DESC, d.doc_key ASC LIMIT ?""",
                    (*parameters, candidate_limit)).fetchall()
        best: dict[str, sqlite3.Row] = {}
        for row in candidates:
            cluster = row["cluster_key"]
            if cluster not in best or (float(row["rank"]), row["doc_key"]) < (
                float(best[cluster]["rank"]), best[cluster]["doc_key"]
            ):
                best[cluster] = row
        selected_candidates = sorted(best.values(), key=lambda row: (float(row["rank"]), row["doc_key"]))
        if not fts_query:
            with self._connect() as connection:
                available = {r["doc_key"]: r["available_at"] for r in connection.execute(
                    "SELECT doc_key, available_at FROM documents WHERE doc_key IN ({})".format(",".join("?" for _ in best)), tuple(best)).fetchall()}
            selected_candidates.sort(key=lambda row: (available.get(row["doc_key"], ""), row["doc_key"]), reverse=True)
        selected_candidates = selected_candidates[(page - 1) * limit: page * limit]
        selected_keys = [row["doc_key"] for row in selected_candidates]
        placeholders = ",".join("?" for _ in selected_keys)
        with self._connect() as connection:
            hydrated_rows = connection.execute(
                f"""
                SELECT d.*, e.*, json_extract(e.response_json, '$.metadata') AS metadata_json
                FROM documents d
                JOIN evidence e ON e.evidence_id = d.parent_evidence_id
                WHERE d.doc_key IN ({placeholders})
                """,
                selected_keys,
            ).fetchall()
            hydrated = {row["doc_key"]: row for row in hydrated_rows}
            siblings = {
                row["cluster_key"]: tuple(r["doc_key"] for r in connection.execute(
                    "SELECT doc_key FROM documents WHERE cluster_key=? AND doc_key<>? ORDER BY doc_key",
                    (row["cluster_key"], row["doc_key"])).fetchall())
                for row in selected_candidates
            }
        results = tuple(TemporalSearchResult(
            evidence=self._search_evidence_from_row(hydrated[row["doc_key"]]),
            rank=float(row["rank"]),
            document=TemporalDocument(
                doc_key=row["doc_key"], parent_evidence_id=hydrated[row["doc_key"]]["parent_evidence_id"],
                title=hydrated[row["doc_key"]]["title"], body=hydrated[row["doc_key"]]["body"],
                source_domain=hydrated[row["doc_key"]]["source_domain"],
                canonical_url=hydrated[row["doc_key"]]["canonical_url"],
                published_at=parse_timestamp(hydrated[row["doc_key"]]["published_at"])
                if hydrated[row["doc_key"]]["published_at"] else None,
                available_at=parse_timestamp(hydrated[row["doc_key"]]["available_at"]),
                doc_kind=hydrated[row["doc_key"]]["doc_kind"],
                siblings=siblings[row["cluster_key"]],
            ),
        ) for row in selected_candidates)
        return TemporalSearchResponse(
            results=results,
            manifest=SearchManifest(
                query=query, as_of=parsed_as_of,
                ranker_version="temporal-document-v2",
                corpus_hash=current_hash,
                evidence_ids=tuple(result.evidence.evidence_id for result in results),
                page=page, limit=limit, date_from=start, date_to=end, source=source,
            ),
        )

    def fetch_document(self, doc_key: str, *, as_of: datetime, page: int = 1, page_chars: int = 4000) -> dict[str, Any]:
        """Read one eligible normalized document in bounded sequential character pages."""
        if page < 1 or not 1 <= page_chars <= 4000:
            raise ValueError("page must be positive and page_chars must be between 1 and 4000")
        with self._connect() as connection:
            row = connection.execute("""SELECT d.*, e.observed_at, e.ingested_at, e.evidence_id,
                e.event_at, e.source_published_at
                FROM documents d JOIN evidence e ON e.evidence_id=d.parent_evidence_id
                WHERE d.doc_key=? AND d.available_at <= ?""", (doc_key, format_timestamp(parse_timestamp(as_of)))).fetchone()
        if row is None:
            raise KeyError(f"unknown or ineligible document: {doc_key}")
        body = row["body"]
        start = (page - 1) * page_chars
        chunk = body[start:start + page_chars]
        if not chunk and start >= len(body):
            raise ValueError("page is beyond document body")
        return {"doc_key": doc_key, "title": row["title"], "body": chunk, "page": page,
                "page_chars": page_chars, "has_more": start + len(chunk) < len(body),
                "source": row["source_domain"], "available_at": row["available_at"],
                "published_at": row["published_at"], "event_at": row["event_at"],
                "observed_at": row["observed_at"], "ingested_at": row["ingested_at"],
                "evidence_id": row["evidence_id"]}

    def corpus_overview(self, *, as_of: datetime, source: str | None = None) -> dict[str, Any]:
        cutoff = format_timestamp(parse_timestamp(as_of))
        predicates = ["d.available_at <= ?"]
        params: list[Any] = [cutoff]
        if source:
            predicates.append("(d.source_domain = ? OR e.source = ? OR d.source_domain LIKE ?)")
            params.extend((source, source, f"%{source}%"))
        with self._connect() as connection:
            rows = connection.execute(f"SELECT d.source_domain, d.available_at FROM documents d JOIN evidence e ON e.evidence_id=d.parent_evidence_id WHERE {' AND '.join(predicates)} ORDER BY d.available_at", params).fetchall()
        domains = sorted({row["source_domain"] for row in rows})
        return {"as_of": cutoff, "source": source, "document_count": len(rows),
                "source_counts": {domain: sum(row["source_domain"] == domain for row in rows) for domain in domains},
                "date_span": {"from": rows[0]["available_at"][:10] if rows else None, "to": rows[-1]["available_at"][:10] if rows else None},
                "corpus_hash": self.corpus_hash(as_of=as_of)}

    @staticmethod
    def _search_evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
        """Build the compatibility evidence view from normalized columns only."""
        return EvidenceRecord(
            evidence_id=row["evidence_id"], tool=row["tool"], request_key=row["request_key"],
            request=json.loads(row["request_json"]), response={
                "title": row["title"], "text": row["body"],
                "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            },
            artifact_hash=row["artifact_hash"], available_at=parse_timestamp(row["available_at"]),
            observed_at=parse_timestamp(row["observed_at"]), ingested_at=parse_timestamp(row["ingested_at"]),
            fidelity=row["fidelity"], event_at=parse_timestamp(row["event_at"]) if row["event_at"] else None,
            source_published_at=parse_timestamp(row["source_published_at"]) if row["source_published_at"] else None,
            source=row["source"], is_error=bool(row["is_error"]),
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
    def _rubric_from_row(row: sqlite3.Row) -> ScenarioRubricRecord:
        return ScenarioRubricRecord(
            scenario_id=row["scenario_id"],
            material_evidence_ids=tuple(json.loads(row["material_evidence_ids_json"])),
            useful_evidence_ids=tuple(json.loads(row["useful_evidence_ids_json"])),
            artifact_hash=row["artifact_hash"],
            created_at=parse_timestamp(row["created_at"]),
            material_document_keys=tuple(json.loads(row["material_document_keys_json"])) if "material_document_keys_json" in row else (),
            useful_document_keys=tuple(json.loads(row["useful_document_keys_json"])) if "useful_document_keys_json" in row else (),
        )

    @staticmethod
    def _research_run_from_row(row: sqlite3.Row) -> ResearchRunRecord:
        return ResearchRunRecord(
            run_id=row["run_id"],
            scenario_id=row["scenario_id"],
            decision=row["decision"],
            report_artifact_hash=row["report_artifact_hash"],
            completed_at=parse_timestamp(row["completed_at"]),
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
    """Turn user text into a conservative FTS5 query without FTS syntax injection.

    Terms combine with OR because agents write long natural-language queries;
    requiring every term (AND) silently matched nothing. bm25 ranking still
    puts documents matching more terms first, and the caller's evidence-id
    tie-break keeps results deterministic.
    """
    return " OR ".join(f'"{token}"' for token in re.findall(r"[\w]+", query, flags=re.UNICODE))


def _normalized_evidence_ids(values: Iterable[str], name: str) -> tuple[str, ...]:
    raw = tuple(values)
    if any(not isinstance(value, str) or not value for value in raw):
        raise ValueError(f"{name} must contain non-empty evidence IDs")
    return tuple(dict.fromkeys(raw))
