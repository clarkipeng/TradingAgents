"""Atomic backup units for the temporal store and model-budget ledger.

The writer is the only canonical format producer.  A unit is a directory
containing SQLite snapshots, a referenced-artifact tar, and a manifest.  The
``temporal-latest`` symlink is the sole publication point.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from collections.abc import Iterable
from contextlib import ExitStack, contextmanager
from pathlib import Path

UNIT_NAME = "temporal-latest"
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "temporal.sqlite3"
BUDGET_NAME = "model-budget.sqlite"
ARTIFACTS_NAME = "artifacts.tar"
SCHEMA_VERSION = 2


class BackupError(RuntimeError):
    """A backup/restore gate refused an unsafe or incomplete operation."""


@contextmanager
def _exclusive_lock(path: Path):
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("active_writer") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _sync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
        target_db.execute("PRAGMA journal_mode=DELETE")
        result = target_db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError("sqlite integrity check failed")
        target_db.commit()
    finally:
        target_db.close()
        source_db.close()


def _catalog_members(database: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(database)
    try:
        try:
            rows = connection.execute("SELECT content_hash, path, byte_length FROM artifacts ORDER BY content_hash").fetchall()
        except sqlite3.Error as exc:
            raise BackupError("artifact catalog unavailable") from exc
    finally:
        connection.close()
    members: list[dict[str, object]] = []
    for content_hash, relative, byte_length in rows:
        relative_path = _safe_relative(str(relative))
        members.append({"path": (Path("artifacts") / relative_path).as_posix(), "sha256": content_hash, "byte_length": byte_length})
    return members


def _artifact_members(store_dir: Path, database: Path) -> list[dict[str, object]]:
    members = _catalog_members(database)
    root = (store_dir / "artifacts").resolve()
    for member in members:
        relative = _safe_relative(str(member["path"]))
        path = (store_dir / relative).resolve()
        if not path.is_file() or root not in path.parents:
            raise BackupError("referenced artifact is missing")
        if _sha256(path) != member["sha256"] or path.stat().st_size != member["byte_length"]:
            raise BackupError("referenced artifact failed hash validation")
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
    unit = Path(unit).resolve(strict=True)
    payload = _manifest(unit)
    for name in (DATABASE_NAME, BUDGET_NAME):
        connection = sqlite3.connect(f"file:{unit / name}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise BackupError("backup sqlite integrity failed")
        finally:
            connection.close()
    expected_members = _catalog_members(unit / DATABASE_NAME)
    catalog = {str(item["path"]): item for item in expected_members}
    with tarfile.open(unit / ARTIFACTS_NAME, "r") as archive:
        names = []
        for item in archive.getmembers():
            relative = _safe_relative(item.name)
            if item.islnk() or item.issym() or not item.isfile():
                raise BackupError("backup archive contains unsafe member")
            name = relative.as_posix()
            expected_member = catalog.get(name)
            if expected_member is None or item.size != expected_member["byte_length"]:
                raise BackupError("backup artifact membership mismatch")
            digest = hashlib.sha256()
            with archive.extractfile(item) as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_member["sha256"]:
                raise BackupError("backup artifact hash failed")
            names.append(name)
    if expected_members != payload.get("artifacts", []):
        raise BackupError("backup catalog membership mismatch")
    expected = sorted(str(item["path"]) for item in expected_members)
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
    budget = Path(budget_path) if budget_path else store_dir / BUDGET_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        for name in ("mutator.lock", "portfolio-chain.lock"):
            locks.enter_context(_exclusive_lock(store_dir / name))
        incoming = backup_dir / f".{UNIT_NAME}.{uuid.uuid4().hex}.partial"
        incoming.mkdir()
        try:
            _sqlite_snapshot(database, incoming / DATABASE_NAME)
            _sqlite_snapshot(budget, incoming / BUDGET_NAME)
            members = _artifact_members(store_dir, incoming / DATABASE_NAME)
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
            for item in incoming.iterdir():
                _sync_path(item)
            _sync_path(incoming)
            published = backup_dir / f"unit-{uuid.uuid4().hex}"
            incoming.replace(published)
            latest_tmp = backup_dir / f".{UNIT_NAME}.{uuid.uuid4().hex}.link"
            latest_tmp.symlink_to(published.name)
            os.replace(latest_tmp, backup_dir / UNIT_NAME)
            _sync_path(backup_dir)
            for prior in backup_dir.glob("unit-*"):
                if prior != published and re.fullmatch(r"unit-[0-9a-f]{32}", prior.name):
                    shutil.rmtree(prior, ignore_errors=True)
            return backup_dir / UNIT_NAME / DATABASE_NAME
        except BaseException:
            shutil.rmtree(incoming, ignore_errors=True)
            raise


def restore_unit(unit: Path, destination: Path, *, providers_disabled: bool = True) -> Path:
    """Restore a verified unit into an empty destination via a staging dir."""
    if not providers_disabled:
        raise BackupError("providers_disabled_required")
    unit = Path(unit).resolve(strict=True)
    payload = verify_unit(unit)
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise BackupError("restore_destination_not_empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination.parent))
    try:
        (staging / "artifacts").mkdir()
        shutil.copy2(Path(unit) / DATABASE_NAME, staging / DATABASE_NAME)
        shutil.copy2(Path(unit) / BUDGET_NAME, staging / BUDGET_NAME)
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
        # Flush the complete restored tree before replacing its directory.
        directories = [staging]
        for item in staging.rglob("*"):
            if item.is_file():
                _sync_path(item)
            elif item.is_dir():
                directories.append(item)
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            _sync_path(directory)
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def install_mirror(unit: Path, destination: Path) -> Path:
    """Replace a mirror under one owner, preserving recovery across a rename."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.with_name(destination.name + ".previous")
    with _exclusive_lock(destination.with_name("." + destination.name + ".sync.lock")):
        # Recover the only gap between the two directory renames if a prior
        # process stopped there. Never discard that last complete mirror.
        if previous.exists() and not destination.exists():
            previous.rename(destination)
        with tempfile.TemporaryDirectory(prefix=".mirror-", dir=destination.parent) as scratch:
            restored = restore_unit(unit, Path(scratch) / "store")
            if previous.exists():
                shutil.rmtree(previous)
            if destination.exists():
                destination.rename(previous)
            try:
                restored.rename(destination)
                _sync_path(destination.parent)
            except OSError:
                if previous.exists() and not destination.exists():
                    previous.rename(destination)
                raise
            if previous.exists():
                shutil.rmtree(previous)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-unit")
    verify.add_argument("unit", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("unit", type=Path)
    restore.add_argument("destination", type=Path)
    mirror = sub.add_parser("mirror")
    mirror.add_argument("unit", type=Path)
    mirror.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-unit":
            verify_unit(args.unit)
        elif args.command == "mirror":
            install_mirror(args.unit, args.destination)
        else:
            restore_unit(args.unit, args.destination)
    except (BackupError, OSError, ValueError, sqlite3.Error, tarfile.TarError):
        print("backup_operation_failed")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
