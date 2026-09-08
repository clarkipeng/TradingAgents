import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradingagents.model_budget import DailyModelBudget
from tradingagents.temporal import TemporalStore
from tradingagents.store_backup import BackupError, backup_store, restore_unit


def _store(tmp_path: Path) -> tuple[Path, Path, str, str]:
    store = tmp_path / "temporal"
    temporal = TemporalStore(store)
    evidence = temporal.record(
        "fixture.tool", {"ticker": "NVDA"}, {"value": "first"},
        available_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    artifact_hash = temporal.put_artifact(b"evidence", "application/octet-stream")
    budget = DailyModelBudget(store, "2026-09-07", max_tokens=100, max_cost="10")
    budget.reserve("ambiguous-call", estimated_tokens=10, estimated_cost="0.10")
    temporal.close()
    return store, tmp_path / "backups", evidence.evidence_id, artifact_hash


def test_backup_failure_keeps_last_valid_unit(tmp_path: Path) -> None:
    store, backups, _evidence_id, artifact_hash = _store(tmp_path)
    latest = backup_store(store, backups)
    first_hash = hashlib.sha256(latest.read_bytes()).hexdigest()
    (store / "artifacts" / artifact_hash[:2] / artifact_hash[2:]).unlink()
    with pytest.raises(BackupError, match="artifact"):
        backup_store(store, backups)
    assert hashlib.sha256(latest.read_bytes()).hexdigest() == first_hash
    assert (backups / "temporal-latest" / "manifest.json").is_file()


def test_successful_replacement_prunes_superseded_units(tmp_path: Path) -> None:
    store, backups, _evidence_id, _artifact_hash = _store(tmp_path)
    backup_store(store, backups)
    backup_store(store, backups)
    assert len(list(backups.glob("unit-*"))) == 1


def test_restore_is_verified_and_preserves_ambiguous_budget_reservation(tmp_path: Path) -> None:
    store, backups, evidence_id, artifact_hash = _store(tmp_path)
    backup_store(store, backups)
    restored = tmp_path / "restored"
    restore_unit(backups / "temporal-latest", restored)
    restored_store = TemporalStore(restored)
    assert restored_store.get_evidence(evidence_id).response == {"value": "first"}
    assert restored_store.read_artifact(artifact_hash) == b"evidence"
    restored_store.close()
    restored_budget = DailyModelBudget(restored, "2026-09-07", max_tokens=100, max_cost="10")
    prior = restored_budget.reserve("ambiguous-call", estimated_tokens=99, estimated_cost="9")
    assert prior.estimated_tokens == 10
    assert str(prior.estimated_cost) == "0.10"

    with pytest.raises(BackupError, match="providers_disabled"):
        restore_unit(backups / "temporal-latest", tmp_path / "other", providers_disabled=False)


def test_restore_refuses_nonempty_destination_without_replacing_it(tmp_path: Path) -> None:
    store, backups, _evidence_id, _artifact_hash = _store(tmp_path)
    backup_store(store, backups)
    destination = tmp_path / "valid"
    destination.mkdir()
    (destination / "sentinel").write_text("keep")
    with pytest.raises(BackupError, match="not_empty"):
        restore_unit(backups / "temporal-latest", destination)
    assert (destination / "sentinel").read_text() == "keep"
