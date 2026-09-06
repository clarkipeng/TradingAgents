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


def replay_portfolio_day(
    store: Any,
    scenario_id: str,
    *,
    replay_worker: Any,
    replay_cio: Any,
) -> dict[str, Any]:
    """Replay every named stream through caller-owned real graph entrypoints.

    Factories stay outside this small coordinator so it cannot accidentally
    construct a live provider.  Each callback must report consumed LLM/tool
    counts; a partial tape is therefore a hard replay failure.
    """
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise KeyError(f"unknown scenario: {scenario_id}")
    manifest = scenario.metadata.get("replay_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("portfolio scenario has no replay manifest")
    validate_portfolio_replay_manifest(manifest)
    workers = []
    for stream in manifest["workers"]:
        result = replay_worker(stream["ticker"], stream["run_id"])
        _assert_consumed(stream, result)
        workers.append(result)
    cio = replay_cio(manifest["cio"]["run_id"])
    _assert_consumed(manifest["cio"], cio)
    return {"scenario_id": scenario_id, "workers": workers, "cio": cio}


def _assert_consumed(stream: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    if result.get("llm_call_count") != stream["llm_call_count"]:
        raise ValueError("portfolio replay did not consume its complete LLM stream")
    if result.get("tool_call_count") != stream["tool_call_count"]:
        raise ValueError("portfolio replay did not consume its complete tool stream")
    if result.get("llm_digest") != stream["llm_digest"]:
        raise ValueError("portfolio replay consumed a different LLM stream")
    if result.get("tool_digest") != stream["tool_digest"]:
        raise ValueError("portfolio replay consumed a different tool stream")
