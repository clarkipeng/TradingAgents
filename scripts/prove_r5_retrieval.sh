#!/bin/sh
set -eu

# This proof intentionally copies the live corpus before opening it. The MCP
# client and its server therefore cannot write the live store.
proof_dir=$(mktemp -d "${TMPDIR:-/tmp}/tradingagents-r5-proof.XXXXXX")
trap 'rm -rf "$proof_dir"' EXIT
cp -R /Users/clarkpeng/.tradingagents/temporal "$proof_dir/temporal"

PROOF_STORE="$proof_dir/temporal" uv run python - <<'PY'
import json
import os
import subprocess
import sys

from tradingagents.temporal import TemporalStore, build_evidence_brief
from tradingagents.temporal.retriever import search_payload

root = os.environ["PROOF_STORE"]
store = TemporalStore(root)
scenario = store.get_scenario("nvda-2026-08-18")
if scenario is None:
    raise SystemExit("proof scenario not found")
response = store.search("NVDA", as_of=scenario.as_of, limit=3)
brief = build_evidence_brief(store, "NVDA", scenario.as_of, 3)
assert brief == search_payload(response)

client = subprocess.Popen(
    [sys.executable, "-m", "cli.temporal_mcp", "--store", root, "--scenario", scenario.scenario_id],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

def call(request_id, method, params):
    client.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n").encode())
    client.stdin.flush()
    header = client.stdout.readline()
    assert header.startswith(b"Content-Length: "), header
    length = int(header.split(b":", 1)[1])
    assert client.stdout.readline() == b"\r\n"
    return json.loads(client.stdout.read(length))

call(1, "initialize", {"protocolVersion": "2024-11-05"})
mcp = call(2, "tools/call", {"name": "search", "arguments": {"query": "NVDA", "limit": 3}})["result"]["structuredContent"]
assert mcp == brief
key = mcp["results"][0]["doc_key"]
call(3, "tools/call", {"name": "fetch", "arguments": {"doc_key": key}})
call(4, "tools/call", {"name": "overview", "arguments": {}})
client.stdin.close()
client.wait(timeout=10)
assert client.returncode == 0, client.stderr.read()
with store._connect() as connection:
    searches = connection.execute(
        "SELECT count(*) FROM search_traces WHERE scenario_id=? AND mode='mcp'",
        (scenario.scenario_id,),
    ).fetchone()[0]
    tools = [row[0] for row in connection.execute(
        "SELECT tool FROM tool_traces WHERE scenario_id=? AND mode='mcp' ORDER BY sequence",
        (scenario.scenario_id,),
    )]
print("identical_tool_brief_mcp=true")
print(f"mcp_search_traces={searches}")
print(f"mcp_tool_traces={tools}")
print(f"copied_store={root}")
PY
