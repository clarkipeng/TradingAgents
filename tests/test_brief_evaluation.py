import json
import os
import subprocess
import sys

import pytest
import tiktoken
from tiktoken.registry import ENCODINGS

from tradingagents.brief_evaluation import compare_frozen_briefs

_FIXTURE_ENCODING = '''
import tiktoken
from tiktoken.registry import ENCODINGS
ENCODINGS["o200k_base"] = tiktoken.Encoding(
    name="fixture-bytes", pat_str=r"(?s).+",
    mergeable_ranks={bytes([n]): n for n in range(256)}, special_tokens={},
)
'''


@pytest.fixture(autouse=True)
def prepared_tokenizer(monkeypatch):
    # Byte-level fixture is supplied in memory; CI need not download a BPE
    # file to test comparison plumbing. Cold-cache refusal is a subprocess.
    monkeypatch.setitem(ENCODINGS, "o200k_base", tiktoken.Encoding(
        name="fixture-bytes", pat_str=r"(?s).+",
        mergeable_ranks={bytes([n]): n for n in range(256)}, special_tokens={},
    ))


def test_comparison_is_frozen_and_requires_a_fresh_model_gate():
    artifact = compare_frozen_briefs("AAPL [evidence:1]", "AAPL [evidence:1]", scenario_id="sealed-day")
    assert artifact["baseline_sha256"] == artifact["candidate_sha256"]
    assert artifact["quality_gate"] == "pending_fresh_model_evaluation"
    assert artifact["baseline_tokens"] > 0


def test_command_writes_only_the_requested_local_artifact(tmp_path):
    baseline = tmp_path / "baseline.txt"
    candidate = tmp_path / "candidate.txt"
    output = tmp_path / "comparison.json"
    baseline.write_text("baseline", encoding="utf-8")
    candidate.write_text("candidate", encoding="utf-8")
    script = _FIXTURE_ENCODING + '\nimport runpy\nrunpy.run_path("scripts/compare_analyst_briefs.py", run_name="__main__")\n'
    subprocess.run([sys.executable, "-c", script, "--baseline", str(baseline), "--candidate", str(candidate), "--scenario-id", "frozen", "--output", str(output)], check=True)
    assert json.loads(output.read_text())["quality_gate"] == "pending_fresh_model_evaluation"


def test_cold_tokenizer_cache_fails_without_network(tmp_path):
    script = '''
import socket
from tradingagents.brief_evaluation import compare_frozen_briefs
def forbidden(*args, **kwargs):
    raise AssertionError("comparison attempted network")
socket.socket.connect = forbidden
try:
    compare_frozen_briefs("before", "after", scenario_id="frozen")
except ValueError as error:
    assert "offline_tokenizer_unavailable" in str(error)
else:
    raise AssertionError("comparison accepted absent tokenizer")
'''
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10,
                   env={**os.environ, "TIKTOKEN_CACHE_DIR": str(tmp_path / "empty")})
