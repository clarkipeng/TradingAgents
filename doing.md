# What's happening right now

Updated: 2026-09-05 early morning. I keep this file current - check it anytime.

## Milestone: the first sealed 30-stock trading days exist
Aug 18, 19, and 20 completed and sealed on the cloud - the first ever. Before this, no 30-stock day had survived anywhere.
Getting here took a chain of five real fixes, each found by measurement: a memory balloon in the search index (8GB, OS kill), a database journal downgrade from my own store copy (readers strangled writers), the day's own recordings invalidating the search cache (minutes-long rebuilds, dozens per day), slow shared CPUs (the Python side runs single-core because of the GIL - now dedicated fast cores), and the growing archive (72k -> 242k documents across the window) making the per-search drift check ever more expensive - now maintained incrementally.

## The track record exists: 13 sealed days, Aug 18 - Sep 4
Total +0.26% vs SPY +0.36%. Too short to mean anything about returns; it proves the machinery.
One real strategy bug found in it and fixed: 8 of 13 days went to 100% cash because the CIO's proposed weights summed a hair over the limit and the whole plan was rejected into a fallback that liquidated everything.
Now: small overages scale down proportionally (exposure can only shrink), and a genuinely rejected plan holds the book instead of selling it - the honest null action, and the cheap one (no fee churn).
See it: `tradingagents temporal-portfolio-report` (on the cloud store).

## Costs per month, roughly
- X data (your "big" tier choice): ~$450 - dominates everything.
- AI research: ~$2-3 per trading day (~$60).
- Cloud machines + disk: ~$70-95.
- Cloud Postgres (the news middle layer): ~$30-70 - planned to be removed after a clean week, the biggest safe cut.
Everything else this week made days cheaper by not wasting them: no more OOM re-runs, no more liquidate-and-rebuy fee churn.

## Waste pass done (Sunday)
- BRK.B is finally quotable (broker dot vs Yahoo dash - it failed every single day before).
- Rebalancing no longer chases sub-0.2% jitter (fees were pure waste).
- The CIO now reads 5x more of the research it pays for, without the constant "Hold" noise.
- Machine memory gets measured Monday and downsized if the data agrees.
- Next structural cut (after Monday proves clean): fold the X collector into the trader machine and drop the Postgres middle layer (~$30-70/month + one moving part).

## Running right now
Nothing. Monday 5:45pm the scheduled day runs hands-off on the fixed build - the first true end-to-end unattended day.

## Cloud migration: DONE (Sep 2)
Everything runs on Fly now - two machines: the X/news collector, and the trader (owns the evidence database on its own disk; polling, 5:45pm trading day, discovery, import, rotating backups; a pause switch for the trading day when I need to run manual chains).
The laptop is read-only; all its jobs parked reboot-proof. `scripts/sync_store_down.sh` pulls the nightly cloud backup as your local mirror.
The overnight schedule has already proven itself twice unattended (capture, import, backup all ran while sessions were down).

Small hardening still to do: artifacts in the backup rotation, a writer-stall alarm, failure notifications to your phone.

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
