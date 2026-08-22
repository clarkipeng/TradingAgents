# Capture Ops Repair Plan

Status: proposed, not yet implemented. Evidence for every finding is cited inline.

## Findings

### F1. X capture was never enabled (environment gap)

`resolve_sources()` (`tradingagents/poller.py:274-291`) appends the `x` source
only when `X_BEARER_TOKEN` is present in the process environment. The poller
never loads `.env`. The local media store
(`~/.tradingagents/media-poller.sqlite3`) contains zero `x` fetch runs and zero
`watermark:x:*` state across all recorded cycles: the token existed in `.env`
but was never exported into the daemon's environment.

### F2. Daily temporal-capture has never succeeded

All three launchd runs logged `Captured 0/276 tool calls` (exit 1). Every tool
call failed with root exception class `OperationalError`, and `tool_traces`
contains zero rows for those run IDs - evidence sealing failed alongside the
fetches. Uniform failure across unrelated providers indicates the shared step
(the SQLite seal write) is the failure point, most plausibly lock contention
with the poller daemon's temporal-store mirror, which runs through 20:00 ET
while capture fires at 17:15 local. The one-mutator-at-a-time rule is
documented but not enforced by any lock.

Secondary noise in the same runs: Reddit RSS `HTTPError` (rate limiting),
yfinance "possibly delisted" responses, StockTwits `URLError`.

### F3. Poller daemon is dead and unrestartable

No poller process is running; no launchd job exists for it. Last collection
cycle completed Aug 22 07:31 UTC. It stops silently and stays stopped.

### F4. Chronically failing keyless sources

`reddit` fails every cycle (`ProviderTransientError`); `bluesky` and
`truthsocial` fail every cycle (`ProviderResponseError`). Truthsocial is
Cloudflare-gated per `media_sources.py:14` and cannot succeed without a token;
it burns a fetch slot per ticker per cycle for nothing.

## Plan

### P0 - Confirm the F2 root cause (no live provider calls)

Reproduce on a copy of the canonical store:

1. Copy `~/.tradingagents/temporal/` to a scratch directory.
2. Open a writer connection holding an exclusive transaction.
3. Run `capture_daily_market_research` against the copy with mocked fetchers.
4. Expect the observed signature: per-call `sqlite3.OperationalError`, zero
   traces sealed.

Check whether `TemporalStore` sets `busy_timeout` / `PRAGMA journal_mode=WAL`
on open; record the actual behavior. This determines whether the fix below
needs a timeout, a lock file, or both.

### P1 - Fixes (each with a test)

1. **Store resilience**: `TemporalStore` connections set
   `busy_timeout` (>= 5s) and verify WAL mode on open. Writers tolerate
   transient contention instead of failing the whole universe.
2. **Single-mutator invariant made mechanical**: an advisory `flock` on
   `<store>/mutator.lock` acquired by (a) the daily capture command and (b) the
   poller's temporal mirror around each receipt write batch. Whoever holds the
   lock mutates; the other waits (with the new busy_timeout as backstop). No
   scheduling coordination required - the invariant holds by construction.
3. **Poller environment**: a `scripts/run_media_poller.sh` wrapper that
   exports the existing `.env` (token names only, values never echoed) and
   execs the poller daemon. Verify with `--sources` resolution dry-run that
   `x` is in the resolved set before any network call.
4. **Daemon supervision**: launchd plist `com.tradingagents.media-poller` with
   `KeepAlive.SuccessfulExit=false` and trading-hours awareness left to the
   poller's own gate. The daemon self-idles off-hours instead of exiting.
5. **Source hygiene**: drop `truthsocial` from the default keyless set (it
   cannot succeed unauthenticated). Leave `reddit` in with its existing
   graceful-degradation path; revisit backoff only if 429s persist after the
   cycle volume drops.

### P2 - Validation package (the gate before "X is ready")

Deliverables, in order:

1. One manual poller cycle: `x` posts present in the media store, mirrored as
   eligible temporal documents with availability clocks, searchable via
   `temporal_search`, budget receipt under the $1.48/day cap.
2. A forced capture run (single ticker, store copy first, then canonical)
   completing N/N with traces sealed while the poller daemon is live -
   proving the lock fix under real contention.
3. Both schedulers installed and armed, with the user flipping the final
   switch.

## Non-goals

- No changes to replay semantics, ranking, or scenario sealing.
- No new social sources beyond enabling the already-built `x` stage.
