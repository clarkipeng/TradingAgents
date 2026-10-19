# TradingAgents recovery archive

Preservation snapshot in the existing `clarkipeng/TradingAgents` fork, selected by the user on 2026-10-18 before removing local TradingAgents checkouts. The fork is public. Saved work lives under `archive/2026-10-18/` branches and `archive-2026-10-18/` tags; its existing default branch is unchanged. GitHub Actions are disabled. Nothing was deployed during archival.

## Restore code

```sh
git clone https://github.com/clarkipeng/TradingAgents.git
cd TradingAgents
git switch --track origin/archive/2026-10-18/agent-trading-simulation-backtest
```

`archive/2026-10-18/agent-trading-simulation-backtest` contains London's integrated audit fixes and latest shutdown documentation. The final source checkpoint is `bb7156d` (archival status documentation; code remains paused). `archive/2026-10-18/archive/canonical-wip-2026-10-18` preserves the older canonical checkout's 8 modified and 18 untracked source files without changing its original `main` branch. `archive/2026-10-18/cdt/8224-ta-audit-lead` includes its previously uncommitted final handoff (`63dff52`). All other local branch tips and tags are retained separately; unfinished or rejected experiments have not been merged. Remote-tracking tips are preserved under `archive/2026-10-18/archive/remote-tips/`.

The `git-history.bundle` file additionally preserves original Conductor/Codex checkpoint refs listed in `historical-refs.json`. It predates the new archival commits, which are on the remote branches. To recover every original ref, run `git clone --mirror git-history.bundle recovered.git` from this index branch.

See [fork-refs.json](fork-refs.json) for exact destination refs and hashes (`git-refs.json` describes the earlier private backup), [worktree-inventory.json](worktree-inventory.json) for the original eight checkouts and dirty-file inventory, and [canonical-snapshot.json](canonical-snapshot.json) for the WIP snapshot. Do not treat every worker branch as approved code. In particular, `archive/2026-10-18/cdt/bf9a-ta-luna-p4-resum` retains rejected P5 work for recovery only.

## What was done and why

The September audit work tightened the trading decision/execution lifecycle, replayed the production graph without external access, added shared model-spend admission, fixed storage/backup recovery, and reduced duplicate data calls. The final combined source was verified on September 8 with 1,782 tests plus 71 subtests, 102 deployment-wrapper tests, and Ruff. One optional test was skipped and 14 service-backed tests were excluded. These checks were historical, not rerun for archival. See the code branch's `docs/audit-repairs-explained.md`, `doing.md`, and `docs/audit-fix-plan-2026-09-06.md`.

The full 33-finding audit remains paused at the user's request. Scheduling/readiness and alert policies, captured-world and retention semantics, representative performance measurements, fresh model-quality evidence, rollout/rollback proof, and legacy seal assessment remain unfinished. No worker is assigned further implementation. The user owns any decision to resume after restoration.

Cloud trading was verified stopped on September 16: both `app` and `trader` process groups had zero Fly Machines. The temporal volume and Postgres were preserved. That observation is historical; archival did not recheck or change cloud infrastructure. Local audit wakeups were disabled and the program remains cancelled/paused. Restoring this repository must not automatically resume workloads.

## Local evidence and excluded material

`workspace-artifacts/` contains copied work notes, audit evidence, logs, review artifacts, attachments, and worker handoffs. Known credentials and connection strings are redacted from exported copies. Raw `.env` files, the credential-bearing subscription response, and local agent permissions are excluded. Original copies are retained separately in `~/Documents/TradingAgents-archive-2026-10-18/local-only/`, outside all TradingAgents checkouts. Preserve that directory when deleting the repository. It also holds an original Git history bundle.

Dependency environments, bytecode, test/lint caches, and generated package metadata are reproducible and excluded. Small local research SQLite databases in the collected artifacts are preserved as consistent SQLite backup snapshots; see `database-verification.json`. Cloud data stores remain where they were; they are not part of this Git archive. Conductor and Sightmesh conversation history remains with those applications; the archive retains branch/workspace/session IDs and handoffs rather than copying credential-sensitive raw conversation stores.

## Workspace retirement

`conductor-workspaces.json` and `sightmesh-workspaces.json` record the initial inventory. Calgary was already archived. London must be archived by the user in Conductor after preservation: current Conductor support does not expose an agent workspace-control API. Sightmesh has 24 of 26 TradingAgents workspaces archived, including 14 archived during this request. `ta-sol-execution-v2` and `ta-luna-p4-resume` remain blocked by the service's unconfirmed detached-tool cleanup. Both have zero running execution processes and all their work is preserved. See `sightmesh-retirement.json` and `sightmesh-final.json`. No implementation agents were woken, and no original repository or branch was deleted.

The backup was verified by a fresh GitHub clone, exact remote-ref comparison, Git integrity checks, all 281 artifact checksums, five restored SQLite integrity checks, and restoration of the original Git bundle (421 refs plus eight checkout-head commit objects). See `remote-verification.json`. Local credentials and raw evidence remain in the external `local-only/` directory described above. GitHub Actions remain disabled. The earlier private recovery repository remains archived as an additional verified backup. Verification against this fork is recorded in `fork-verification.json`; the earlier `remote-verification.json` refers to the private backup.
