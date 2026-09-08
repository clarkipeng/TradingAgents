"""Atomic backup units for the temporal store and model-budget ledger.

The writer is the only canonical format producer.  A unit is a directory
containing SQLite snapshots, a referenced-artifact tar, and a manifest.  The
``temporal-latest`` symlink is the sole publication point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path

import fcntl

UNIT_NAME = "temporal-latest"
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "temporal.sqlite3"
BUDGET_NAME = "model-budget.sqlite"
ARTIFACTS_NAME = "artifacts.tar"
SCHEMA_VERSION = 2


class BackupError(RuntimeError):
    """A backup/restore gate refused an unsafe or incomplete operation."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise BackupError("unsafe archive member")
    return path


def _sqlite_snapshot(source: Path, target: Path) -> None:
    if not source.is_file():
        raise BackupError("required ledger is missing")
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    target_db = sqlite3.connect(target)
    try:
        source_db.backup(target_db, pages=256, sleep=0.05)
        result = target_db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError("sqlite integrity check failed")
        target_db.commit()
    finally:
        target_db.close()
        source_db.close()


def _artifact_members(store_dir: Path, database: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(database)
    try:
        try:
            rows = connection.execute("SELECT content_hash, path, byte_length FROM artifacts ORDER BY content_hash").fetchall()
        except sqlite3.Error as exc:
            raise BackupError("artifact catalog unavailable") from exc
    finally:
        connection.close()
    root = store_dir.resolve()
    members: list[dict[str, object]] = []
    for content_hash, relative, byte_length in rows:
        relative_path = _safe_relative(str(relative))
        path = (store_dir / relative_path).resolve()
        if not path.is_file() or root not in path.parents:
            raise BackupError("referenced artifact is missing")
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
        if actual_hash != content_hash or actual_size != byte_length:
            raise BackupError("referenced artifact failed hash validation")
        members.append({"path": relative_path.as_posix(), "sha256": actual_hash, "byte_length": actual_size})
    return members


def _write_artifact_archive(store_dir: Path, target: Path, members: Iterable[dict[str, object]]) -> None:
    with tarfile.open(target, "w") as archive:
        for member in members:
            relative = _safe_relative(str(member["path"]))
            source = store_dir / relative
            archive.add(source, arcname=relative.as_posix(), recursive=False)


def _manifest(unit: Path) -> dict[str, object]:
    path = unit / MANIFEST_NAME
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BackupError("unsupported backup schema")
    for name in (DATABASE_NAME, BUDGET_NAME, ARTIFACTS_NAME):
        if not (unit / name).is_file():
            raise BackupError("backup unit is incomplete")
    for name, key in ((DATABASE_NAME, "database_sha256"), (BUDGET_NAME, "budget_sha256"), (ARTIFACTS_NAME, "artifacts_sha256")):
        if payload.get(key) != _sha256(unit / name):
            raise BackupError("backup hash verification failed")
    return payload


def verify_unit(unit: Path) -> dict[str, object]:
    """Verify unit hashes, SQLite integrity, and artifact membership."""
    payload = _manifest(unit)
    for name in (DATABASE_NAME, BUDGET_NAME):
        connection = sqlite3.connect(f"file:{unit / name}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("backup sqlite integrity failed")
        finally:
            connection.close()
    with tarfile.open(unit / ARTIFACTS_NAME, "r") as archive:
        names = []
        for item in archive.getmembers():
            relative = _safe_relative(item.name)
            if item.islnk() or item.issym() or not item.isfile():
                raise BackupError("backup archive contains unsafe member")
            names.append(relative.as_posix())
    expected = sorted(str(item["path"]) for item in payload.get("artifacts", []))
    if names != expected:
        raise BackupError("backup artifact membership mismatch")
    return payload


def backup_store(store_dir: Path, backup_dir: Path, *, budget_path: Path | None = None) -> Path:
    """Create and atomically publish one complete backup unit.

    A non-blocking store mutator lock is required.  An active writer causes a
    fixed refusal rather than an unowned cutover.
    """
    store_dir = Path(store_dir)
    backup_dir = Path(backup_dir)
    database = store_dir / DATABASE_NAME
    budget = Path(budget_path) if budget_path else store_dir.parent / BUDGET_NAME
    lock_path = store_dir / "mutator.lock"
    backup_dir.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("active_writer") from exc
        members = _artifact_members(store_dir, database)
        incoming = backup_dir / f".{UNIT_NAME}.{uuid.uuid4().hex}.partial"
        incoming.mkdir()
        try:
            _sqlite_snapshot(database, incoming / DATABASE_NAME)
            _sqlite_snapshot(budget, incoming / BUDGET_NAME)
            _write_artifact_archive(store_dir, incoming / ARTIFACTS_NAME, members)
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "database": DATABASE_NAME,
                "database_sha256": _sha256(incoming / DATABASE_NAME),
                "budget": BUDGET_NAME,
                "budget_sha256": _sha256(incoming / BUDGET_NAME),
                "artifacts_archive": ARTIFACTS_NAME,
                "artifacts_sha256": _sha256(incoming / ARTIFACTS_NAME),
                "artifacts": members,
            }
            (incoming / MANIFEST_NAME).write_text(json.dumps(manifest, sort_keys=True) + "\n")
            verify_unit(incoming)
            published = backup_dir / f"unit-{uuid.uuid4().hex}"
            incoming.replace(published)
            latest_tmp = backup_dir / f".{UNIT_NAME}.{uuid.uuid4().hex}.link"
            latest_tmp.symlink_to(published.name)
            os.replace(latest_tmp, backup_dir / UNIT_NAME)
            return backup_dir / UNIT_NAME / DATABASE_NAME
        except BaseException:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def restore_unit(unit: Path, destination: Path, *, providers_disabled: bool = True,
                 budget_destination: Path | None = None) -> Path:
    """Restore a verified unit into an empty destination via a staging dir."""
    if not providers_disabled:
        raise BackupError("providers_disabled_required")
    payload = verify_unit(Path(unit))
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise BackupError("restore_destination_not_empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination.parent))
    try:
        (staging / "artifacts").mkdir()
        shutil.copy2(Path(unit) / DATABASE_NAME, staging / DATABASE_NAME)
        shutil.copy2(Path(unit) / BUDGET_NAME, staging.parent / BUDGET_NAME)
        with tarfile.open(Path(unit) / ARTIFACTS_NAME, "r") as archive:
            for item in archive.getmembers():
                relative = _safe_relative(item.name)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        for item in payload.get("artifacts", []):
            path = staging / _safe_relative(str(item["path"]))
            if _sha256(path) != item["sha256"]:
                raise BackupError("restored artifact hash failed")
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)
        # The budget ledger belongs beside the temporal store.  Its copy is
        # installed only after the complete store staging tree is verified.
        restored_budget = staging.parent / BUDGET_NAME
        if restored_budget.exists():
            restored_budget.replace(budget_destination or (destination.parent / BUDGET_NAME))
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-unit")
    verify.add_argument("unit", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("unit", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--budget-destination", type=Path)
    args = parser.parse_args(argv)
    if args.command == "verify-unit":
        verify_unit(args.unit)
    else:
        restore_unit(args.unit, args.destination, budget_destination=args.budget_destination)
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
