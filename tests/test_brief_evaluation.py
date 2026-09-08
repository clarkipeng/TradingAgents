import json
import subprocess
import sys

from tradingagents.brief_evaluation import compare_frozen_briefs


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
    subprocess.run([sys.executable, "scripts/compare_analyst_briefs.py", "--baseline", str(baseline), "--candidate", str(candidate), "--scenario-id", "frozen", "--output", str(output)], check=True)
    assert json.loads(output.read_text())["quality_gate"] == "pending_fresh_model_evaluation"
