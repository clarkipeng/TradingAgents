import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from store_backup import BackupError, backup_store, restore_unit


def _store(tmp_path: Path) -> tuple[Path, Path]:
    store = tmp_path / "temporal"
    store.mkdir()
    artifact = store / "artifacts" / "aa" / "fixture"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"evidence")
    digest = hashlib.sha256(b"evidence").hexdigest()
    db = sqlite3.connect(store / "temporal.sqlite3")
    db.execute("CREATE TABLE artifacts (content_hash TEXT, path TEXT, byte_length INTEGER)")
    db.execute("CREATE TABLE values_table (value TEXT)")
    db.execute("INSERT INTO values_table VALUES ('first')")
    db.execute("INSERT INTO artifacts VALUES (?, ?, ?)", (digest, "artifacts/aa/fixture", 8))
    db.commit()
    db.close()
    budget = sqlite3.connect(tmp_path / "model-budget.sqlite")
    budget.execute("CREATE TABLE model_reservations (day TEXT, call_id TEXT, tokens INTEGER, cost TEXT, PRIMARY KEY(day, call_id))")
    budget.execute("INSERT INTO model_reservations VALUES ('2026-09-07', 'ambiguous-call', 10, '0.10')")
    budget.commit()
    budget.close()
    return store, tmp_path / "backups"


def test_backup_failure_keeps_last_valid_unit(tmp_path: Path) -> None:
    store, backups = _store(tmp_path)
    latest = backup_store(store, backups)
    first_hash = hashlib.sha256(latest.read_bytes()).hexdigest()
    (store / "artifacts" / "aa" / "fixture").unlink()
    with pytest.raises(BackupError, match="artifact"):
        backup_store(store, backups)
    assert hashlib.sha256(latest.read_bytes()).hexdigest() == first_hash
    assert (backups / "temporal-latest" / "manifest.json").is_file()


def test_restore_is_verified_and_preserves_ambiguous_budget_reservation(tmp_path: Path) -> None:
    store, backups = _store(tmp_path)
    backup_store(store, backups)
    restored = tmp_path / "restored"
    restore_unit(backups / "temporal-latest", restored)
    copy = sqlite3.connect(restored / "temporal.sqlite3")
    assert copy.execute("SELECT value FROM values_table").fetchone() == ("first",)
    copy.close()
    budget = sqlite3.connect(tmp_path / "model-budget.sqlite")
    assert budget.execute("SELECT call_id FROM model_reservations").fetchone() == ("ambiguous-call",)
    budget.close()
    assert (restored / "artifacts" / "aa" / "fixture").read_bytes() == b"evidence"

    with pytest.raises(BackupError, match="providers_disabled"):
        restore_unit(backups / "temporal-latest", tmp_path / "other", providers_disabled=False)


def test_restore_refuses_nonempty_destination_without_replacing_it(tmp_path: Path) -> None:
    store, backups = _store(tmp_path)
    backup_store(store, backups)
    destination = tmp_path / "valid"
    destination.mkdir()
    (destination / "sentinel").write_text("keep")
    with pytest.raises(BackupError, match="not_empty"):
        restore_unit(backups / "temporal-latest", destination)
    assert (destination / "sentinel").read_text() == "keep"
