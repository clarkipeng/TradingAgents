"""Canonical request, evidence, and outcome models without framework imports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from .clock import format_timestamp


class TemporalMode(str, Enum):
    LIVE = "live"
    LIVE_CAPTURE = "live_capture"
    REPLAY = "replay"


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return format_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalize(value.value)
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize JSON-compatible data deterministically for hashing and storage."""
    return json.dumps(_normalize(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def request_key(tool: str, request: Mapping[str, Any]) -> str:
    """Return a stable key for one canonical tool request."""
    payload = canonical_json({"tool": tool, "request": request}).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class EvidenceRecord:
    """An immutable record of one captured tool response."""

    evidence_id: str
    tool: str
    request_key: str
    request: dict[str, Any]
    response: Any
    artifact_hash: str
    available_at: datetime
    observed_at: datetime
    ingested_at: datetime
    fidelity: str
    event_at: datetime | None = None
    source_published_at: datetime | None = None
    source: str | None = None
    is_error: bool = False


@dataclass(frozen=True)
class TemporalOutcome:
    """A tool value plus capture provenance when one exists."""

    value: Any
    evidence: EvidenceRecord | None


@dataclass(frozen=True)
class LLMCallRecord:
    """Framework-neutral persisted trace of one model invocation."""

    call_id: str
    run_id: str
    request: dict[str, Any]
    response: Any | None
    error: dict[str, str] | None
    started_at: datetime
    completed_at: datetime | None
    scenario_id: str | None = None
    mode: str | None = None
    temporal_run_id: str | None = None
    temporal_sequence: int | None = None


@dataclass(frozen=True)
class ScenarioSnapshot:
    """Immutable scenario input that would otherwise come from mutable host state."""

    scenario_id: str
    name: str
    state: Any
    artifact_hash: str
    captured_at: datetime


@dataclass(frozen=True)
class ScenarioDefinition:
    """The sealed time, basis, and metadata for one reproducible world."""

    scenario_id: str
    as_of: datetime
    basis: str
    metadata: dict[str, Any]
    corpus_hash: str
    artifact_hash: str
    created_at: datetime
    capture_run_id: str | None = None


@dataclass(frozen=True)
class ScenarioRubricRecord:
    """Immutable human relevance labels attached to one sealed scenario."""

    scenario_id: str
    material_evidence_ids: tuple[str, ...]
    useful_evidence_ids: tuple[str, ...]
    artifact_hash: str
    created_at: datetime
    material_document_keys: tuple[str, ...] = ()
    useful_document_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchRunRecord:
    """Immutable final decision/report pointer for one completed agent run."""

    run_id: str
    scenario_id: str
    decision: str | None
    report_artifact_hash: str | None
    completed_at: datetime


@dataclass(frozen=True)
class TemporalDocument:
    doc_key: str
    parent_evidence_id: str
    title: str
    body: str
    source_domain: str
    canonical_url: str | None
    published_at: datetime | None
    available_at: datetime
    doc_kind: str
    siblings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemporalSearchResult:
    """One evidence item returned by the owned temporal full-text index."""

    evidence: EvidenceRecord
    rank: float
    document: TemporalDocument | None = None

    @property
    def doc_key(self) -> str:
        return self.document.doc_key if self.document else self.evidence.evidence_id


@dataclass(frozen=True)
class SearchManifest:
    """The replayable description of one owned temporal search result set."""

    query: str
    as_of: datetime
    ranker_version: str
    corpus_hash: str
    evidence_ids: tuple[str, ...]
    index_state_hash: str = ""
    tie_break: str = "evidence_id/doc_key"
    page: int = 1
    limit: int = 10
    date_from: str | None = None
    date_to: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class TemporalSearchResponse:
    results: tuple[TemporalSearchResult, ...]
    manifest: SearchManifest


@dataclass(frozen=True)
class ToolTraceRecord:
    """One tool result selected during a temporal run."""

    trace_id: str
    run_id: str
    sequence: int
    scenario_id: str | None
    mode: str
    tool: str
    request_key: str
    evidence_id: str | None
    invoked_at: datetime


@dataclass(frozen=True)
class SearchTraceRecord:
    """One owned temporal search issued during an agent run."""

    trace_id: str
    run_id: str
    sequence: int
    scenario_id: str | None
    mode: str
    manifest: SearchManifest
    invoked_at: datetime
