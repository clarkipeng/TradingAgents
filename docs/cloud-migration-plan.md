# Cloud migration plan: everything on Fly

Status: proposed 2026-09-02, not started.
Goal: the laptop leaves the critical path entirely.
One Fly machine owns the canonical evidence store and runs capture, discovery, and the daily trading run.

## Why

The cloud half (X + global news on `tradagent`) has run for a week without incident.
Every operational failure so far came from the laptop: machine sleep killing runs, an OS memory kill, a zombie launchd plist re-arming a 14-hour lock wedge, and a teammate renice slowing capture.
Capture is capture-or-lose, and the trading day has a wall-clock deadline; both need an always-on host.
Moving everything to one machine also deletes the cloud-Postgres-to-laptop sync seam, which removes a whole class of drift and lock contention by construction.

## Target architecture

One Fly machine (upgrade `tradagent`: shared-cpu-2x, 4GB RAM, 10GB volume, ~$25-35/month plus existing Postgres).
The canonical temporal store (SQLite, currently 2.3GB) lives on the Fly volume.
Single mutator stays an invariant by construction: exactly one machine, same flock discipline as today.
All writers run on that machine as supervised processes under one scheduler:

- market-hours ticker poller (stocktwits, news, macro) - continuous
- X roster + trends + global news - continuous (unchanged, but now writes straight into the temporal store; the Postgres intermediate becomes unnecessary)
- daily discovery (GDELT + Wayback) - nightly
- portfolio trading day - weekdays 17:45 ET
- generational index rebuild - only when re-armed deliberately, with a lock-holder liveness alarm

The laptop becomes a read-only consumer: a `sync_store_down.sh` pulls a store snapshot for dev, replay, and reports.

## Phases

### Phase 0 - prerequisites (no code)
Wait for the 10-day backfill to finish locally; do not move a store mid-write.
Snapshot the store and verify corpus integrity (`verify_scenario_corpus`).

### Phase 1 - the machine and the store (half day)
Add a 10GB volume to `tradagent` and mount it; resize to 4GB RAM.
Upload the store once (`fly sftp` or restore from snapshot), then verify integrity remotely.
Add LLM keys as Fly secrets (X bearer and DB URL are already there).

### Phase 2 - move the writers (half day)
Run the full poller (not `--global-only`) against the volume store.
Point the X/global collector at the temporal store directly, keeping Postgres writes on temporarily as a shadow during transition.
Add a process supervisor + scheduler in the image (the poller already loops; discovery and the portfolio day become scheduled entries).
Timezone pinned in the container so "17:45 weekday" means what it means today.

### Phase 3 - cutover (one evening)
Freeze all laptop writers (move plists out of LaunchAgents - file-level, learned the hard way).
Final store upload, final integrity check, enable cloud schedules.
Next trading day runs entirely in the cloud; verify with `verify_x_cycle.py` and the portfolio day seal.

### Phase 4 - safety net (half day, non-optional)
Nightly store backup off-machine (volume snapshots plus a compressed SQLite backup to object storage) - the corpus is irreplaceable.
Health check extended: last-write-age alarm and lock-holder liveness (a wedged writer must surface in minutes, not 14 hours).
Failure notification to the user (telegram bridge) when a day fails or capture stalls.
Deploy gate: refuse deploys while a portfolio day is mid-run (days are atomic anyway, but do not waste the spend).

### Phase 5 - decommission (later)
After a clean week, drop the Postgres media intermediate and the laptop sync jobs.
Laptop keeps only `sync_store_down.sh` and dev tooling.

## Risks

- Volume is single-region durability: mitigated by Phase 4 off-machine backups before cutover, not after.
- Memory sizing: the reindex OOM-adjacent kill argues for 4GB and for never scheduling the rebuild alongside a trading day.
- One-time 2.3GB upload: slow but boring; done twice (rehearsal + cutover).
- Rollback: the laptop plists still exist in the disabled directory; re-arming them restores today's setup in minutes.

## Estimate

Roughly one focused day of work plus one evening cutover, spread over two days.
Running cost delta: ~$25-35/month for the bigger machine and volume; Postgres eventually drops away.
