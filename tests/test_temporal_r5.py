"""Focused R5 regression coverage: one payload contract and MCP traces."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone

from tradingagents.temporal import (
    TemporalContext,
    TemporalMode,
    TemporalStore,
    build_evidence_brief,
    temporal_context,
)
from tradingagents.temporal.retriever import search_payload
from tradingagents.temporal_adapters.langchain import create_temporal_search_tool

UTC = timezone.utc


def _fixture_store(tmp_path):
    store = TemporalStore(tmp_path)
    as_of = datetime(2025, 1, 10, tzinfo=UTC)
    store.record(
        "corpus.document", {"source": "example.test"},
        {"title": "NVDA earnings growth", "text": "NVIDIA revenue and earnings increased."},
        available_at=datetime(2025, 1, 2, tzinfo=UTC), source="example.test",
    )
    store.record(
        "corpus.document", {"source": "example.test"},
        {"title": "NVDA product launch", "text": "NVIDIA announced a new accelerator."},
        available_at=datetime(2025, 1, 3, tzinfo=UTC), source="example.test",
    )
    store.seal_scenario("r5-fixture", as_of=as_of, basis="test")
    return store, as_of


def _mcp_call(process, request_id, method, params=None):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    process.stdin.write((json.dumps(request) + "\n").encode())
    process.stdin.flush()
    header = process.stdout.readline()
    assert header.startswith(b"Content-Length: ")
    length = int(header.split(b":", 1)[1])
    assert process.stdout.readline() == b"\r\n"
    return json.loads(process.stdout.read(length))


def test_brief_with_run_identity_records_a_search_trace(tmp_path):
    """Evidence surfaced by the injected brief must count toward coverage
    exactly like an agent-issued search - otherwise the brief arm's
    evidence_coverage scores as if the run saw nothing."""
    store, as_of = _fixture_store(tmp_path)

    untraced = build_evidence_brief(store, "NVDA", as_of, 2)
    assert store.list_search_traces("brief-run") == []

    traced = build_evidence_brief(
        store, "NVDA", as_of, 2,
        run_id="brief-run", scenario_id=None, mode="replay",
    )
    assert traced == untraced  # identity never changes the payload
    traces = store.list_search_traces("brief-run")
    assert len(traces) == 1
    assert traces[0].manifest.query == "NVDA"


def test_initial_state_threads_the_brief_as_text_and_traces_it(tmp_path):
    from tradingagents.graph.propagation import Propagator

    store, as_of = _fixture_store(tmp_path)
    context = TemporalContext.at(
        TemporalMode.REPLAY, as_of, store=store, run_id="brief-arm-run"
    )

    with temporal_context(context):
        disabled = Propagator(config={"temporal": {"mode": "replay"}}).create_initial_state(
            "NVDA", "2026-08-18"
        )
        assert disabled["evidence_brief"] == ""

        enabled = Propagator(
            config={"temporal": {"mode": "replay", "evidence_brief": True, "evidence_brief_k": 2}}
        ).create_initial_state("NVDA", "2026-08-18")

    assert isinstance(enabled["evidence_brief"], str)
    assert "evidence" in enabled["evidence_brief"]
    traces = store.list_search_traces("brief-arm-run")
    assert len(traces) == 1
    assert traces[0].manifest.query == "NVDA"


def test_tool_brief_and_retriever_share_identical_payload(tmp_path):
    store, as_of = _fixture_store(tmp_path)
    response = store.search("NVDA", as_of=as_of, limit=2)
    brief = build_evidence_brief(store, "NVDA", as_of, 2)
    context = TemporalContext.at(TemporalMode.REPLAY, as_of, store=store, run_id="tool-run")
    with temporal_context(context):
        tool_payload = json.loads(create_temporal_search_tool(store).invoke({"query": "NVDA", "limit": 2}))
    assert tool_payload == brief == search_payload(response)


def test_real_stdio_mcp_client_records_full_trace(tmp_path):
    store, _as_of = _fixture_store(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-m", "cli.temporal_mcp", "--store", str(tmp_path), "--scenario", "r5-fixture"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _mcp_call(process, 1, "initialize", {"protocolVersion": "2024-11-05"})
        search = _mcp_call(process, 2, "tools/call", {"name": "search", "arguments": {"query": "NVDA", "limit": 1}})
        payload = search["result"]["structuredContent"]
        assert payload == build_evidence_brief(store, "NVDA", store.get_scenario("r5-fixture").as_of, 1)
        doc_key = payload["results"][0]["doc_key"]
        _mcp_call(process, 3, "tools/call", {"name": "fetch", "arguments": {"doc_key": doc_key}})
        _mcp_call(process, 4, "tools/call", {"name": "overview", "arguments": {}})
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        stderr = process.stderr.read().decode()
        process.stdout.close()
        process.stderr.close()
    assert process.returncode == 0, stderr
    with store._connect() as connection:
        search_traces = connection.execute(
            "SELECT query, scenario_id, mode FROM search_traces WHERE scenario_id=?", ("r5-fixture",)
        ).fetchall()
        tool_traces = connection.execute(
            "SELECT tool, scenario_id, mode FROM tool_traces WHERE scenario_id=? ORDER BY sequence", ("r5-fixture",)
        ).fetchall()
    assert [(row["query"], row["scenario_id"], row["mode"]) for row in search_traces] == [("NVDA", "r5-fixture", "mcp")]
    assert [row["tool"] for row in tool_traces] == ["temporal_fetch", "corpus_overview"]
    assert all(row["mode"] == "mcp" for row in tool_traces)
