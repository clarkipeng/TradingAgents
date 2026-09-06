"""Versioned, explicit membership for a captured portfolio replay.

The manifest deliberately names independent worker streams.  Their completion
order is not replay order; each stream is consumed only by its named worker.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from tradingagents.temporal.models import canonical_json

PORTFOLIO_REPLAY_MANIFEST_VERSION = 1


def stream_digest(records: Iterable[Any]) -> str:
    """Hash stable stream facts without depending on database row ordering."""
    facts = [
        {"id": getattr(record, "call_id", getattr(record, "trace_id", "")),
         "sequence": getattr(record, "sequence", None),
         "completed": str(getattr(record, "completed_at", ""))}
        for record in records
    ]
    return hashlib.sha256(canonical_json(facts).encode("utf-8")).hexdigest()


def build_portfolio_replay_manifest(
    store: Any,
    *,
    parent_run_id: str,
    tickers: Iterable[str],
    graph_version: str = "portfolio-graph-v1",
    prompt_version: str = "portfolio-cio-v1",
    execution_policy: str = "close-legacy-v1",
) -> dict[str, Any]:
    """Describe the exact streams required by one captured portfolio day."""
    workers = []
    for ticker in sorted(set(tickers)):
        run_id = f"{parent_run_id}:research:{ticker}"
        calls = store.list_llm_calls(None, temporal_run_id=run_id)
        traces = store.list_tool_traces(run_id)
        workers.append({
            "ticker": ticker,
            "run_id": run_id,
            "llm_call_count": len(calls),
            "llm_digest": stream_digest(calls),
            "tool_call_count": len(traces),
            "tool_digest": stream_digest(traces),
        })
    cio_calls = store.list_llm_calls(None, temporal_run_id=parent_run_id)
    cio_traces = store.list_tool_traces(parent_run_id)
    manifest = {
        "version": PORTFOLIO_REPLAY_MANIFEST_VERSION,
        "parent_run_id": parent_run_id,
        "workers": workers,
        "cio": {
            "run_id": parent_run_id,
            "llm_call_count": len(cio_calls),
            "llm_digest": stream_digest(cio_calls),
            "tool_call_count": len(cio_traces),
            "tool_digest": stream_digest(cio_traces),
        },
        "graph_version": graph_version,
        "prompt_version": prompt_version,
        "execution_policy": execution_policy,
    }
    manifest["digest"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    return manifest


def validate_portfolio_replay_manifest(manifest: Mapping[str, Any]) -> None:
    """Reject incomplete or ambiguous manifests before any replay starts."""
    required = {"version", "parent_run_id", "workers", "cio", "digest"}
    if set(manifest) < required or manifest.get("version") != PORTFOLIO_REPLAY_MANIFEST_VERSION:
        raise ValueError("unsupported portfolio replay manifest")
    worker_ids = [worker.get("run_id") for worker in manifest["workers"]]
    if len(worker_ids) != len(set(worker_ids)) or manifest["parent_run_id"] in worker_ids:
        raise ValueError("portfolio replay streams are ambiguous")
    unsigned = {key: value for key, value in manifest.items() if key != "digest"}
    expected = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
    if manifest["digest"] != expected:
        raise ValueError("portfolio replay manifest digest mismatch")
