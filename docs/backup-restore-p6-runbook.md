# Backup, restore, and local P6 gate

The canonical backup is a versioned directory selected by the atomic
`backups/temporal-latest` symlink. Each unit contains:

- `temporal.sqlite3`, copied with SQLite's backup API;
- `model-budget.sqlite` inside the store, copied with the same API;
- `artifacts.tar`, containing every artifact referenced by the database;
- `manifest.json`, with database, budget, archive, and per-artifact hashes.

`tradingagents.store_backup.backup_store()` refuses an active mutator lock, missing or
corrupt referenced artifact, missing budget ledger, SQLite integrity failure,
or archive membership mismatch. It publishes only after full verification;
the prior symlink remains selected on failure. `tradingagents.store_backup.restore_unit()` verifies the
unit, rejects absolute/parent-traversal/symlink archive members, restores into
an empty staging destination, and retains every model reservation—including
ambiguous calls. Providers are disabled by contract during restore.

The local reader is `scripts/sync_store_down.sh`. It downloads the four unit
members, runs `python3 -m tradingagents.store_backup verify-unit`, restores to a sibling
staging directory, and only then replaces the mirror. The previous mirror is
kept as `<mirror>.previous` until successful completion. Pre-versioned
`temporal-latest.sqlite3` files are not silently treated as complete units;
they lack the budget and membership contract and must be migrated by an
operator.

Before any P6 local cutover, create an evidence JSON file with fixed values:

```json
{"preflight":"passed","cutover":"verified","rollback":"verified",
 "backup_restore":"verified","providers":"disabled","observed_at":"..."}
```

`verify_local_p6_evidence()` returns `p6_local_gate_verified` only for that
complete local proof. Missing observations produce a fixed refusal outcome;
the function never invokes deployment or provider APIs.
