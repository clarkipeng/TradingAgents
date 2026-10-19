# TradingAgents system map (2026-08-31, base 1a70f38)

One sentence: a point-in-time evidence machine that captures the market's information world daily, lets multi-agent research run against any captured moment without seeing the future, and now runs a sealed paper-trading portfolio day on top.

## The four layers

### 1. Capture (runs unattended)
- Cloud collector (Fly app `tradagent`, Managed Postgres `polled`): global news hourly, X trend discovery plus topic searches once per UTC day, X cashtag roster (50 tickers x 10 posts) once per UTC day, all budget-receipted.
- Laptop poller (launchd `media-poller`, market hours): per-ticker StockTwits and news plus macro themes and Polymarket odds into `~/.tradingagents/media-poller.sqlite3`. Reddit is dead pending OAuth credentials. Bluesky and truthsocial are retired from the keyless set.
- GDELT daily discovery plus Wayback body bridge (launchd `daily-discovery` 18:30): OR-grouped queries, paced 5.5s with escalating retries.
- Cloud-to-laptop sync (launchd `cloud-media-sync` 19:15): `flyctl mpg proxy` tunnel, idempotent `temporal-media-import`.
- Tool-tape capture (launchd `temporal-capture` weekdays 17:15): live tool calls taped for the 30-ticker universe.

### 2. The temporal store (`~/.tradingagents/temporal`, ~171k documents)
- Evidence rows carry `available_at` = when we observed it; search and fetch refuse anything after a run's `as_of` by construction.
- Single-mutator invariant via flock `write_lock()`; sqlite busy timeout 180s.
- Document clustering is now assigned only by the nightly `temporal-reindex` (launchd 21:30); ingestion files documents self-clustered.
- Scenarios are sealed immutable identities; `verify_scenario_corpus` detects drift.

### 3. Research (replay or live-capture, identical surfaces)
- Graph agents get taped fixed-feed tools, `temporal_search`/`temporal_fetch`/`corpus_overview`, and `x_posts(subject)` over the closed 50-ticker roster.
- Every LLM call tapes via `LangChainTapeRecorder`; every retrieval records traces; coverage and grounding are scored against sealed rubrics.
- Plan A result: retrieval arms strictly dominate fixed feeds; the injected brief captures most of the benefit.

### 4. Portfolio (launchd `portfolio-day` weekdays 17:45)
- `temporal-portfolio-run`: trimmed-graph sweep per ticker, one deep-model CIO call proposing long-only weights under hard caps (invalid proposals fall back to the deterministic allocator), whole-share rebalance, simulator fills at taped quotes, post-fill state sealed as `portfolio.state` evidence, day sealed as scenario `portfolio-<date>` (immutable, reruns skip).
- `temporal-portfolio-report` renders the sealed track record.

## Verification surfaces
- `scripts/verify_x_cycle.py <day>`: cycle terminal, per-category budget caps, roster 50/50, posts landed, mirrored, searchable.
- `scripts/collector_health.sh`: daemon liveness, running-build-vs-worktree drift, day of alerts.
- Full pytest suite: ~1750 tests.

## Known open items (fleet TODO inventory)
1. `verify_x_cycle.py` reads the local media store, but X lives in the cloud Postgres now; it needs `--media-db-url` (or the proxy) so its X and roster checks run against the real store.
2. Wayback body bridge lands 0 bodies every night (`bodies 0/50`); diagnose whether the CDX window, URL normalization, or snapshot availability is at fault.
3. `run_portfolio_day` has no per-day LLM budget cap and no all-quotes-missing guard (weekend or vendor outage day should skip loudly, not trade on nothing).
4. `portfolio_run.py` reaches into `store._connect()` (private); the state and report queries belong on `TemporalStore` as owned methods.
5. Polymarket residual failures (~1/3 of slots, transient schema variant) recur; receipts now carry raise sites - characterize and fix or explicitly tolerate.
6. GDELT groups still get throttled some nights (idempotent retry covers it); consider spreading groups across the night.
7. The 2026-08-19 nvda/tsla/msft scenarios still lack sealed rubrics, blocking coverage scoring on them.
8. Golden full-trace replay of a sealed portfolio day (TapeChatModel) has never been exercised; it is the strongest replay proof we advertise.
9. Two failed 2026-08-31 day attempts left partial taped runs (harmless, unsealed); decide whether stray `llm_calls`/traces without research_runs need a janitor.
10. Docs: no single system overview existed before this map; the repo needs `docs/system-overview.md` per the docs-taste rules.

## Hard guardrails for every fleet worker
- Never write to `~/.tradingagents/*` (live stores); tests use tmp dirs only.
- Never run poller, launchctl, deploys, or `temporal-portfolio-run`; never spend provider or LLM budgets.
- Base branch `agent-trading-simulation-backtest` at 1a70f38; workers write only in their own worktrees.
- Taste authority: `.context/handoffs/fleet/taste-canon.md`. Sentence-per-line Markdown, plain dashes, no agent co-author lines.
- Proof for any code change: the affected test files plus `python -m pytest -q` green in the worktree.
