# TradingAgents system overview

This document describes the implemented system at commit `1a70f38` on 2026-08-31.

The repository implementation is authoritative for behavior, while the fleet system map at `/Users/clarkpeng/Documents/Code/TradingAgents/.context/handoffs/fleet/system-map.md` is the dated inventory that motivated this overview.

Planning documents such as the [future platform architecture](future-platform-architecture.md) are not authority for the current runtime shape.

The overview is fresh only when its implementation links and commit date are checked against the worktree.

## System boundary

TradingAgents captures market information, stores point-in-time evidence, runs multi-agent research against a bounded temporal view, and can execute a sealed paper-trading day in a deterministic simulator.

The system has four operational layers: capture, temporal storage, research, and portfolio simulation.

The [default configuration](../tradingagents/default_config.py) and [CLI composition](../cli/main.py) select providers and commands, while the temporal modules own time-safe evidence behavior.

## Capture

The cloud collector is the Fly app declared in [fly.toml](../fly.toml).

It runs the global-only poller process and writes captured source observations to Managed Postgres through the media-store interfaces in [tradingagents/poller.py](../tradingagents/poller.py) and [tradingagents/dataflows/media_store.py](../tradingagents/dataflows/media_store.py).

The cloud collector uses hourly broad-news cycles and a once-per-UTC-day bounded X discovery cycle, with its source boundary and limits defined by [collector_contract.py](../tradingagents/collector_contract.py) and the environment in [fly.toml](../fly.toml).

The local media poller covers configured ticker media, macro themes, and supported social or market sources through [tradingagents/poller.py](../tradingagents/poller.py).

The [media-poller launcher](../scripts/run_media_poller.sh) supplies the local media database and optional temporal mirror without printing credential values.

The daily discovery launcher invokes the GDELT and Wayback bridge through [run_daily_discovery.sh](../scripts/run_daily_discovery.sh) and the corresponding CLI command in [cli/main.py](../cli/main.py).

The cloud-to-laptop import launcher copies cloud-owned media into the local temporal corpus through [sync_cloud_media.sh](../scripts/sync_cloud_media.sh).

The weekday temporal capture launcher invokes the full capture surface using the configured universe in [run_temporal_capture.sh](../scripts/run_temporal_capture.sh).

The launchd installer schedules that capture at 17:15 local time on weekdays through [install_temporal_launchd.sh](../scripts/install_temporal_launchd.sh).

Capture failures are recorded as source errors where the temporal gateway is in live-capture mode, rather than being converted into successful evidence.

## Temporal evidence store

The [TemporalStore](../tradingagents/temporal/store.py) owns the local corpus, its SQLite metadata, content-addressed artifacts, documents, traces, scenarios, and research-run records.

The authoritative observation timestamp is `available_at`, and the [store record path](../tradingagents/temporal/store.py) persists it with each evidence row.

The temporal invariant is that a search or fetch can observe only documents with `available_at` at or before its timezone-aware `as_of` boundary, enforced by [TemporalRetriever.search](../tradingagents/temporal/retriever.py) and [TemporalStore.fetch_document](../tradingagents/temporal/store.py).

The [UTC clock helpers](../tradingagents/temporal/clock.py) reject timezone-naive timestamps and normalize accepted timestamps to UTC.

The single-mutator invariant is owned by [TemporalStore.write_lock](../tradingagents/temporal/store.py), which serializes capture, import, and reindex writers with an advisory file lock.

Document clustering is assigned by the explicit reindex operation in [TemporalStore.reindex_documents](../tradingagents/temporal/store.py), exposed by the `temporal-reindex` command in [cli/main.py](../cli/main.py).

Search results carry a corpus hash, ranker version, index-state hash, evidence identifiers, and page parameters through [TemporalSearchResponse](../tradingagents/temporal/models.py) and [TemporalRetriever.search](../tradingagents/temporal/retriever.py).

Page requests after the first page require a matching corpus-hash pin, so pagination cannot silently continue against a changed corpus.

Scenarios are immutable identities sealed by [TemporalStore.seal_scenario](../tradingagents/temporal/store.py), and [verify_scenario_corpus](../tradingagents/temporal/store.py) detects corpus drift against the sealed hash.

## Research and replay

The [TemporalContext](../tradingagents/temporal/runtime.py) owns a research run's mode, virtual clock, scenario identity, and optional source tape.

The [TemporalGateway](../tradingagents/temporal/gateway.py) is the boundary for tool calls in live, live-capture, and replay modes.

Live-capture calls persist successful responses and source errors as evidence, while replay calls return eligible captured evidence and never call the source again.

Replay refuses missing evidence, replayed source errors, and mismatched full-tape sequences through the gateway's typed errors.

The [LangChain tape recorder](../tradingagents/temporal_adapters/langchain.py) records model calls, and the store records tool and search traces through [record_tool_trace](../tradingagents/temporal/store.py) and [record_search_trace](../tradingagents/temporal/store.py).

Graph agents use the same captured tool surfaces for research and replay through the temporal adapters in [tradingagents/temporal_adapters](../tradingagents/temporal_adapters).

The MCP entry point scopes `temporal_search`, `temporal_fetch`, and `corpus_overview` to one stored scenario in [cli/temporal_mcp.py](../cli/temporal_mcp.py).

## Portfolio simulation

The daily paper-trading lifecycle is owned by [run_portfolio_day](../tradingagents/portfolio_run.py).

It skips a day whose `portfolio-<date>` scenario already exists, researches each ticker, reads the prior sealed state, obtains time-bound quotes, asks one CIO call for target weights, and validates the proposal before creating orders.

An invalid or malformed CIO proposal falls back to the deterministic allocator in [portfolio_run.py](../tradingagents/portfolio_run.py).

The hard portfolio constraints are long-only exposure, a one-times gross limit, and a ten-percent maximum position weight as defined by `DEFAULT_CONSTRAINTS` in [portfolio_run.py](../tradingagents/portfolio_run.py).

The [PortfolioSimulator](../tradingagents/temporal/simulation.py) owns cash accounting, fees, slippage, position updates, and quote-time validation.

The simulator refuses timezone-naive timing, future quotes, invalid prices, overspending, and short sales at its execution boundary.

Post-fill state is recorded as `portfolio.state` evidence by [record_portfolio_state](../tradingagents/portfolio_run.py).

The portfolio day seals its scenario after the state is recorded, and the next day reads the latest eligible sealed state through [portfolio_state_asof](../tradingagents/portfolio_run.py).

The sealed track-record report reads only portfolio-state evidence through [portfolio_report](../tradingagents/portfolio_run.py).

## Verification surfaces

The full test suite is the repository-wide executable check for these contracts.

The X-cycle structural and budget checks are implemented by [verify_x_cycle.py](../scripts/verify_x_cycle.py).

Collector liveness, build drift, and alert-day checks are implemented by [collector_health.sh](../scripts/collector_health.sh).

Temporal retrieval and replay behavior is covered by focused tests including [test_temporal_documents.py](../tests/test_temporal_documents.py), [test_temporal_r5.py](../tests/test_temporal_r5.py), and [test_temporal_full_trace_replay.py](../tests/test_temporal_full_trace_replay.py).

Portfolio accounting and allocation behavior is covered by the portfolio-related tests in [tests](../tests).

This document does not claim that every operational check is currently green, that every source is available, or that every sealed scenario has a rubric.

Those freshness and coverage facts belong to the operational verification surfaces and the dated fleet handoff, not to this stable architecture map.
