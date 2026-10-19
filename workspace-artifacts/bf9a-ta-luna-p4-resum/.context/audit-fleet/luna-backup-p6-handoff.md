# Luna backup/P6 handoff

Date: 2026-09-07

## Changes

- `tradingagents/store_backup.py`: canonical version-2 backup unit with SQLite backup API
  snapshots for temporal and model-budget ledgers, nonblocking mutator-lock
  ownership, real `TemporalStore` artifact-catalog hash/size validation, archive membership
  verification, atomic latest symlink publication, safe staged restore, and
  ambiguous-reservation preservation. Superseded units are pruned only after
  successful publication.
- `tradingagents/cloud_supervisor.py`: delegates backup publication to the
  canonical writer and adds evidence-gated local P6 verification with fixed
  outcomes.
- `scripts/sync_store_down.sh`: reads the versioned unit, verifies/restores
  into siblings, and replaces the mirror only after full validation.
- `docs/backup-restore-p6-runbook.md`: format, restore, old-format, and P6
  evidence contract.

## Proof

`PYTHONPATH=$PWD uv run --with pytest pytest -q tests/test_store_backup.py tests/test_cloud_supervisor.py tests/test_operations.py`
passed: **61 tests**. Python compilation and `sh -n scripts/sync_store_down.sh`
passed. Proof covers missing-artifact failure retaining the prior unit,
verified empty restore, preservation of an ambiguous model reservation,
nonempty-destination refusal, and P6 evidence refusal/acceptance. No provider,
secret, cloud, or deployment action was run.

## Residuals

Parent should integrate this work and run the deployment-script preflight only
with real local evidence. Old filename backups are explicitly rejected as
incomplete rather than silently restored. The shell final directory swap keeps
the prior mirror at `.previous` until success; operators should clean it only
after confirming the new mirror.
