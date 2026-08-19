# Merge and Launch Plan: Temporal Core + X Pipeline + Hacker News

## Current implementation status

- **Local merge complete:** temporal core and `add-x-api-key` are combined in
  `db5d594`; nothing has been pushed from this workspace.
- **Phase 2 boundary:** the temporal SQLite/artifact store is the corpus used
  by capture, replay, search, and trace evaluation. The existing Postgres media
  store remains poller staging until its writer is ported; `temporal-media-import`
  is a one-way bridge for its existing X/Reddit/news rows, using each poller's
  fetch receipt as the temporal availability clock.
- **HN vertical slice complete:** daily capture records one bounded
  `social.hackernews` feed per run, derives per-story `corpus.document` records
  linked to the feed artifact, and `temporal-hn-import` backfills Algolia HN
  stories with their creation clock. `temporal.hacker_news_enabled` exposes the
  fixed feed to news and sentiment analysis without changing the default graph.
- **Reddit archive slice complete:** `temporal-reddit-import` uses bounded
  Arctic Shift post/comment searches over explicit subreddits and dates, stores
  each raw response, and writes per-record documents marked
  `archive-reconstructed`.
- **Search hygiene complete:** owned FTS indexes only `corpus.document` records;
  raw tool tapes (especially market-price payloads) remain replay evidence but
  cannot crowd retrieval.
- **Extended daily capture complete:** `temporal-capture --full-surface` adds
  ticker fundamentals/statements/insiders plus global news, a small macro
  basket, and prediction markets while preserving the lightweight default.
- **Poller-to-corpus projection complete:** setting
  `TRADINGAGENTS_POLLER_TEMPORAL_STORE` mirrors each terminal poller media
  receipt (including X) into per-post temporal documents without altering its
  budget or once-per-day controls. Retiring the poller staging store remains a
  later migration, not a prerequisite for usable temporal search.
- **Scenario evaluation complete:** `temporal-rubric` seals material/useful
  evidence IDs against a scenario; `temporal-score-run` scores one persisted
  replay trace, `temporal-compare-runs` compares two arms, and
  `temporal-compare-repeated-runs` adds modal-decision stability against that
  exact same immutable rubric. Completed replay decisions/reports are sealed
  with the run rather than supplied later as ad hoc files.
- **Scheduler package complete:** the checked-in universe, capture runner, and
  opt-in per-user launchd installer are ready for local installation; no system
  scheduler has been installed from this workspace.
- **Rollout executed (2026-08-18):** canonical corpus at
  `/Users/clarkpeng/.tradingagents/temporal`; launchd job
  `com.tradingagents.temporal-capture` installed (weekdays 17:15, full surface,
  30-symbol universe); corpus seeded with a forward-captured NVDA graph run
  plus HN/Reddit/GDELT/SEC backfill; scenario `nvda-2026-08-18-v2` sealed with
  a 10-material/25-useful rubric.
- **First paired experiment executed** (baseline vs `search_enabled`, pinned
  gpt-5.4/gpt-5.4-mini, 2 repetitions per arm). Running it E2E surfaced and
  fixed four real defects: replay misses crashed runs (now degrade to
  `NO_DATA_AVAILABLE`), the search tool was never advertised in analyst
  prompts, AND-joined FTS matched nothing for realistic queries (now OR +
  bm25, ranker `sqlite-fts5-or-bm25-v2`), and full documents in tool results
  blew the provider request limit (now bounded snippets).
- **First experimental finding:** naive FTS search adds retrieval volume, not
  value - evidence coverage and decisions unchanged, retrieval efficiency
  drops. The harness attributes this cleanly, which is the point.
- **Still pending:** propagate `[evidence:<id>]` citations into the final
  trade decision so citation grounding stops reading null; improve retrieval
  ranking or rubric-corpus alignment before rerunning the search arm; let the
  scheduler accumulate a week of scenarios.
  They remain separate waves because none should fork the canonical corpus.

## Historical starting state

Two real lines of work existed; everything else was a stale duplicate.

| Worktree | Branch | State | Contains |
|---|---|---|---|
| `london` | `agent-trading-simulation-backtest` | **uncommitted**, at `main` tip | Temporal core (`temporal/`, `temporal_adapters/`, `temporal_collectors/`), SEC/Wayback/GDELT importers, capture/replay, FTS search, A/B evaluation, simulator, 15 test files |
| `calgary` | `add-x-api-key` | committed, 40 commits ahead of `main` | X poller (`x_shadow.py`, `x_cycle.py`, `poller.py`), `hacker_news.py`, live `gdelt.py`, `media_store` + 13 Postgres migrations, `research/` evaluation package, backtest/walkforward, Fly deploy infra |

The directories `workspaces/TradingAgents/add-x-api-key/` and `.../agent-trading-simulation-backtest/` are byte-identical clones of calgary and london; delete them after Phase 0 confirms everything is pushed.

Both branches fork from the same `main` tip (`a33fd4c`), so the merge base is clean.
192 files differ on calgary; only 8 files conflict with london's changes.

## Phase 0: preserve state (do first, mechanical)

1. Commit london's temporal work on `agent-trading-simulation-backtest` (logical commits: core, adapters, collectors, CLI, tests, docs).
2. Push both branches to the fork.
3. Remove the two duplicate clone directories once pushes are verified.

Gate: both branches recoverable from the remote; `git status` clean in both worktrees.

## Phase 1: the merge

Merge `add-x-api-key` into `agent-trading-simulation-backtest` in the london worktree.
Calgary is the committed, CI-tested branch; london's smaller diff re-applies on top of it in each conflict.

Per-file conflict resolutions:

| File | Resolution |
|---|---|
| `dataflows/interface.py` | Take calgary's expanded router body (more vendors, hardening) as `_route_to_vendor_live`; keep london's temporal-gateway wrapper as the public `route_to_vendor` |
| `agents/analysts/sentiment_analyst.py` | Take calgary's content; re-wrap its Reddit/StockTwits prefetches in london's `invoke_tool` |
| `agents/analysts/news_analyst.py` | Take calgary's content; no temporal edits needed beyond what `route_to_vendor` already covers |
| `graph/trading_graph.py`, `graph/setup.py` | Take calgary's checkpointer/structured changes; re-apply london's `temporal_context` entry in `propagate()` and the LangChain adapter hookup |
| `default_config.py` | Union: calgary's keys plus london's `temporal` block |
| `cli/main.py` | Union: both branches only add commands |
| `pyproject.toml` / `uv.lock` | Merge dependency lists, then regenerate the lock with `uv lock` |

Gate: full pytest green (union of both suites, ~115 test files), including calgary's `test_architecture_boundaries.py`.
If the boundary tests reject `temporal/` imports, extend the boundary rules deliberately in the same commit, never by weakening the test.

## Phase 2: reconcile duplicated subsystems

One decision per overlap, recorded here so the merge does not silently run two of everything.

| Overlap | Decision |
|---|---|
| Evidence stores: temporal SQLite store vs `media_store` + Postgres migrations | The temporal store is the canonical corpus. `media_store` remains the X poller's staging layer only until Phase 3 ports the poller; then its write path retires. The Postgres migrations stay unused unless the Fly poller is redeployed. |
| GDELT: calgary's live `dataflows/gdelt.py` vs london's archive `temporal_collectors/gdelt.py` | Both stay - live discovery and archive import are different jobs - but now share `dataflows/gdelt_common.py` request normalization and the bounded `provider_http` transport. |
| Evaluation: calgary `research/` (coverage, label, evaluate, outcomes) vs london `temporal/evaluation.py` | London's paired trace-metric harness is the runner. Port calgary's labeling and coverage-rubric pieces into it as the scenario rubric source. Calgary's outcome/decision validation feeds Phase 5 simulation, not the trace harness. |
| Backtest: calgary `backtest.py`/`walkforward.py`/`portfolio_backtest.py` vs london `temporal/simulation.py` | `simulation.py` stays the fill model. Walkforward becomes the scenario iterator when outcome evaluation lands. No unification work now; the only Phase 2 requirement is no import cycles between them. |
| Evidence identity: calgary `evidence_lineage.py` vs temporal content-addressed IDs | Temporal IDs are canonical. Keep `evidence_lineage` helpers inside the poller until its port, then map its `(source, external_id)` identity into evidence metadata. |

Gate: exactly one canonical answer per row above is reflected in imports; no module writes evidence to two stores.

## Phase 3: social and HN capture

1. **Hacker News forward capture:** wire `dataflows/hacker_news.py` through `invoke_tool` as `social.hackernews`, add it to `capture_daily_market_research`, and expose it to the news/sentiment analysts as an opt-in tool.
2. **Hacker News backfill:** new `temporal_collectors/hn_algolia.py` using the Algolia HN search API - the one social source with true historical search - writing per-story `corpus.document` evidence with `available_at` = story creation time, basis `archive-reconstructed`.
3. **X capture:** the poller now mirrors terminal X media receipts to the temporal corpus when `TRADINGAGENTS_POLLER_TEMPORAL_STORE` is set, after (not inside) the existing paid-request commit boundary. This preserves the hard daily budget and one-terminal-attempt-per-day discipline. Forward-only; there is no X backfill. `temporal-media-import` remains available for previously staged rows.
4. **Per-post social documents:** a derivative pass that explodes each captured Reddit/StockTwits/HN/X fetch blob into per-post `corpus.document` records (post clocks, linked to parent evidence by input hash). The fetch blob stays the replay-tape unit; the per-post docs are what FTS ranks and labels point at.
5. **Reddit backfill:** complete. `temporal-reddit-import` uses Arctic Shift post/comment queries, filtered by subreddit + ticker mention, and writes per-record documents with `available_at` = `created_utc` and a retained raw-response artifact.
6. **Full-surface daily capture:** implemented for fundamentals, statements, insiders, global news, macro/FRED, Polymarket, and HN via `temporal-capture --full-surface`. X remains supplied through the poller-media bridge until its writer migrates.
7. **FTS hygiene:** complete. Only `corpus.document` records are indexed; tool blobs remain available for exact replay only.

Gate: one daily capture run records every tool surface for the universe; a backfilled window contains per-post social documents from at least Reddit and HN.

## Phase 4: get it running

1. **Scheduler:** implementation complete in `scripts/run_temporal_capture.sh`, `config/temporal-universe.txt`, and `scripts/install_temporal_launchd.sh`. The installer is explicit and has not been run; the Fly poller redeploy remains optional.
2. **Backfill scenarios:** for a few historical windows (earnings, guidance, launches, macro shocks): SEC filings, Wayback IR/press pages, GDELT discovery, then the bounded `temporal-gdelt-wayback-import` body bridge so headlines have readable article text, plus Reddit/HN archive imports. Seal each as `archive-reconstructed`.
3. **Label a small eval set:** tooling complete. `temporal-rubric` seals per-scenario material/useful evidence IDs, `temporal-score-run` reports one trace, and `temporal-compare-runs` reports A/B deltas; curate the initial 10-20 actual scenarios once the scheduler has accumulated forward capture.
4. **Run the first paired experiment:** current TradingAgents vs one changed prompt/retrieval/graph arm, same scenarios, same pinned model; compare coverage, grounding, efficiency, stability.
5. **Only after trace metrics show signal:** connect decisions to the simulator and add outcome metrics, using both `forward-captured` and non-event-window scenarios to avoid selection bias.

Gate: the daily cron has run unattended for a week with failures visible in its report; one A/B experiment produced attributable per-scenario metric differences.

## Order and risk

Phases 0-1 are one sitting; Phase 2 is decisions plus small refactors; Phase 3 items are independent and parallelizable; Phase 4 runs concurrently with Phase 3 once capture works.

Known risks:
- The merge is large by file count but narrow by conflict; the danger is semantic drift in the 8 shared files, which the union test suite gates.
- Calgary's boundary tests may need deliberate extension for `temporal/` imports.
- Running `media_store` and the temporal store simultaneously past Phase 3 would fork the corpus; the Phase 2 table exists to prevent that.
