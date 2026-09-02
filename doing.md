# What's happening right now

Updated: 2026-09-02 afternoon. I keep this file current - check it anytime.

## Cloud migration: happening now (you said go)
The whole system is moving to Fly today. Progress:
- DONE: new `trader` machine built and deployed (supervisor owns the store and all schedules: polling, capture, 5:45pm trading day, discovery, import, nightly backup).
- DONE: all laptop jobs stopped and parked reboot-proof; laptop is now read-only.
- IN PROGRESS: uploading the 2.1GB evidence database + artifacts to the cloud volume.
- NEXT: supervisor auto-starts once seeded, then the full 10-day backfill runs in the cloud.
The local day-18 attempt this morning had invalidated itself (recording failures from the lock-wedge fallout), so nothing was lost by cutting over.

## The goal
Build a paper-trading system whose results can actually be trusted.
Every day it records the market's information world, has AI agents research 30 stocks using only what was knowable that day, sizes positions with a CIO agent, simulates the trades, and seals the day so it can be replayed identically forever.
Trust comes from the sealing: a day either completes fully and honestly or fails visibly - it can never half-work, lie, or peek at the future.

## Running right now
- **The 10-day backfill, restarted midday Sep 2** (~5-7 hours, ~$25): Aug 18 through Sep 1 in order.
  Last night's attempt got wedged: an old nightly index-rebuild job we thought was disabled re-armed itself, grabbed the database's single-writer lock at 21:30, and held it 14 hours. Day 18's process was killed under the pressure; day 19 sat frozen waiting for the lock (no money spent, nothing corrupted - the safety rules held).
  Fixed: rebuild job now disabled at the file level so it can't come back on reboot; sweep relaunched with sleep prevention on.
  Watch it: `tail -f .tradingagents/portfolio-backfill.log`
  See results after: `tradingagents temporal-portfolio-report`

## Planned: move everything to the cloud
You asked why not run it all on Fly. Agreed - the laptop is the proven weak point.
Full plan: `docs/cloud-migration-plan.md`.
Short version: one bigger Fly machine owns the evidence database and runs capture, discovery, and the daily trading run; the laptop just downloads copies for you to look at.
About one day of work plus an evening cutover, ~$25-35/month extra, starts after tonight's backfill report.
Waiting on your go.

## Just finished (the audit arc)
Three audit rounds found and fixed 10+ real problems; 1,762 tests green.
Trading days are now atomic, ~25-45 minutes, with hard rules (deadline, spend cap, refuse-to-seal below 80% research coverage), and recordings that invalidate the day rather than silently lie.

## Always running (no attention needed)
- Cloud server: X capture (50 stocks daily + trends), global news - never sleeps.
- Laptop: stock news/StockTwits during market hours, nightly news-archive sweep, nightly cloud sync, weekday 5:45pm trading day.

## Next, in order
1. Backfill finishes -> first real track-record report vs SPY.
2. Today's 5:45pm scheduled run may collide with the backfill - if it does, it fails visibly and re-runs; no harm.
3. Then accumulation: each day adds one sealed, replayable trading day.

## Waiting on you (whenever, nothing urgent)
- Yes/no on moving the daily trading run to Fly (recommended).
- Reddit login keys (2-minute paste) - revives the last dead data source.
- Yes/no on capturing Google search rankings (~$1-5/day; can never be backfilled).
- Yes/no on the Plan B experiment ($40-100): testing an outside AI agent against the frozen archive.
