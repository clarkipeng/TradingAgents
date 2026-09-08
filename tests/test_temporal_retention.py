from datetime import datetime, timezone

from tradingagents.temporal import TemporalStore


def test_artifact_reachability_report_preserves_referenced_artifacts(tmp_path):
    store = TemporalStore(tmp_path)
    kept = store.record(
        "fixture.tool", {"ticker": "NVDA"}, {"value": "kept"},
        available_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    orphan = store.put_artifact(b"unreferenced fixture")

    report = store.artifact_reachability_report()

    assert report["reachable_count"] >= 1
    assert report["unreferenced_hashes"] == (orphan,)
    assert kept.evidence_id
    assert report["missing_reachable_hashes"] == ()


def test_artifact_reachability_report_detects_missing_referenced_bytes(tmp_path):
    store = TemporalStore(tmp_path)
    evidence = store.record(
        "fixture.tool", {"ticker": "NVDA"}, {"value": "kept"},
        available_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    artifact_hash = store.get_evidence(evidence.evidence_id).artifact_hash
    (store.artifacts_dir / artifact_hash[:2] / artifact_hash[2:]).unlink()

    assert store.artifact_reachability_report()["missing_reachable_hashes"] == (artifact_hash,)


def test_retention_plan_is_non_destructive_and_protects_runs(tmp_path):
    store = TemporalStore(tmp_path)
    run = store.begin_llm_call("run-protected", {"prompt": "fixture"}, started_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    store.finish_llm_call("run-protected", response={"answer": "fixture"}, completed_at=datetime(2026, 9, 1, 1, tzinfo=timezone.utc))
    before = (tmp_path / "temporal.sqlite3").read_bytes()

    plan = store.retention_plan(
        before=datetime(2026, 9, 2, tzinfo=timezone.utc),
        protected_run_ids=(run.run_id,),
    )

    assert plan["destructive_action"] == "none"
    assert plan["candidate_trace_rows"]["llm_calls"] == 0
    assert plan["inline_payloads_with_artifacts"] == 1
    assert (tmp_path / "temporal.sqlite3").read_bytes() == before


def test_retention_automatically_protects_sealed_child_streams(tmp_path):
    with TemporalStore(tmp_path) as store:
        at = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for call_id, temporal_run_id in [("model-call", "parent:research:A"), ("debug-call", "debug")]:
            store.begin_llm_call(call_id, {"prompt": "fixture"}, temporal_run_id=temporal_run_id, started_at=at)
            store.finish_llm_call(call_id, response={"answer": "fixture"}, completed_at=at)
        store.seal_scenario("day", as_of=at, basis="forward-captured", capture_run_id="parent", metadata={
            "replay_manifest": {"workers": [{"run_id": "parent:research:A"}], "cio": {"run_id": "parent"}},
        })
        plan = store.retention_plan(before=datetime(2026, 9, 2, tzinfo=timezone.utc))
        assert plan["candidate_trace_rows"]["llm_calls"] == 1
        assert plan["protected_run_ids"] == ("parent", "parent:research:A")
        assert store.get_llm_call("model-call") is not None
