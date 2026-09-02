# What's happening right now

Updated: 2026-09-02 midday. I keep this file current - check it anytime.

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

## Open question from you: run it all on Fly?
Capture already lives on Fly (X + global news, 24/7). The daily trading run still lives on the laptop, and the laptop is the proven weak point (sleep, this lock wedge, an OS kill).
My take: yes, move the trading day to the cloud too. It needs the evidence store reachable from the cloud machine plus the LLM keys there - roughly a day of work. Say go and I'll plan it.

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
