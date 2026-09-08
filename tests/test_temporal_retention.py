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
