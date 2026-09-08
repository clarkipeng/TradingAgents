# Backup, restore, and local P6 gate

The canonical backup is a versioned directory selected by the atomic
`backups/temporal-latest` symlink. Each unit contains:

- `temporal.sqlite3`, copied with SQLite's backup API;
- `model-budget.sqlite` inside the store, copied with the same API;
- `artifacts.tar`, containing every artifact referenced by the database;
- `manifest.json`, with database, budget, archive, and per-artifact hashes.

`tradingagents.store_backup.backup_store()` refuses an active mutator or portfolio-chain lock, missing or
corrupt referenced artifact, missing budget ledger, SQLite integrity failure,
or archive membership mismatch. It publishes only after full verification;
the prior symlink remains selected on failure. `tradingagents.store_backup.restore_unit()` verifies the
unit, rejects absolute/parent-traversal/symlink archive members, restores into
an empty staging destination, and retains every model reservation—including
ambiguous calls. Providers are disabled by contract during restore.

The local reader is `scripts/sync_store_down.sh`. It downloads a complete unit
and calls `python3 -m tradingagents.store_backup mirror`. That command verifies
and restores to a sibling staging directory, flushes it, then replaces the
mirror under an exclusive refresh lock. An interrupted directory swap recovers
from `<mirror>.previous`; successful installation removes the previous copy.
The actual shell reader is tested with local transport fixtures, including a
corrupt download that preserves the previous mirror and its spend ledger.
Pre-versioned `temporal-latest.sqlite3` files lack this complete-unit contract
and remain migration inputs rather than automatically promoted backups.

These checks establish local backup and restore behavior. P6 cutover still
requires actual old-owner termination, lease release, reconciled evidence and
spend ledgers, rollback transfer, and representative operating observations.
Record the executed checks and their artifact digests. A JSON object saying
“verified” cannot establish those outcomes. The existing deployment preflight
and health gates remain the release authority.
