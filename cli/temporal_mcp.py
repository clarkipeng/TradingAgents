"""Small dependency-light MCP stdio server for one sealed temporal scenario."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from uuid import uuid4

from tradingagents.temporal import TemporalStore, canonical_json
from tradingagents.temporal.retriever import search_payload


def _message() -> dict | None:
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    if line.startswith(b"Content-Length:"):
        length = int(line.split(b":", 1)[1].strip())
        while sys.stdin.buffer.readline().strip():
            pass
        return json.loads(sys.stdin.buffer.read(length))
    return json.loads(line)


def _send(message: dict) -> None:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
    sys.stdout.buffer.write(b"Content-Length: " + str(len(encoded)).encode() + b"\r\n\r\n" + encoded)
    sys.stdout.buffer.flush()


def _result(request_id, value: object) -> dict:
    text = canonical_json(value)
    normalized = json.loads(text)
    return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}], "structuredContent": normalized}}


def serve(store: TemporalStore, scenario_id: str) -> None:
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise KeyError(f"unknown scenario: {scenario_id}")
    as_of = scenario.as_of
    run_id = f"mcp-{uuid4()}"
    while True:
        request = _message()
        if request is None:
            return
        if "method" not in request:
            continue
        method = request["method"]
        request_id = request.get("id")
        if method == "notifications/initialized":
            continue
        if method == "initialize":
            _send(_result(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "tradingagents-temporal", "version": "1"}}))
            continue
        if method == "tools/list":
            _send(_result(request_id, {"tools": [
                {"name": "search", "description": "Search the scenario corpus", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
                {"name": "fetch", "description": "Fetch a bounded document page", "inputSchema": {"type": "object", "properties": {"doc_key": {"type": "string"}, "page": {"type": "integer"}}, "required": ["doc_key"]}},
                {"name": "overview", "description": "Summarize the scenario corpus", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}}}},
            ]}))
            continue
        if method != "tools/call":
            if request_id is not None:
                _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
            continue
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name == "search":
            response = store.retriever.search(arguments.get("query", ""), as_of=as_of, limit=int(arguments.get("limit", 10)), page=int(arguments.get("page", 1)), corpus_hash_pin=arguments.get("corpus_hash"))
            store.record_search_trace(run_id=run_id, scenario_id=scenario_id, mode="mcp", manifest=response.manifest, invoked_at=datetime.now(timezone.utc))
            value = search_payload(response)
        elif name == "fetch":
            value = {"result": store.fetch_document(arguments["doc_key"], as_of=as_of, page=int(arguments.get("page", 1)))}
            store.record_tool_trace(run_id=run_id, scenario_id=scenario_id, mode="mcp", tool="temporal_fetch", request=arguments, evidence_id=value["result"]["evidence_id"], invoked_at=datetime.now(timezone.utc))
        elif name == "overview":
            value = store.corpus_overview(as_of=as_of, source=arguments.get("source"))
            store.record_tool_trace(run_id=run_id, scenario_id=scenario_id, mode="mcp", tool="corpus_overview", request=arguments, evidence_id=None, invoked_at=datetime.now(timezone.utc))
        else:
            _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "unknown tool"}})
            continue
        _send(_result(request_id, value))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args(argv)
    serve(TemporalStore(args.store), args.scenario)


if __name__ == "__main__":
    main()
