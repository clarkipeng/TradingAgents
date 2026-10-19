# Fleet round-two re-audit at `2438b78`

## Executive result

Round two fully fixes findings 6 and 10, partially fixes findings 1, 4, 5, and 8, and does not fully fix finding 2.
The production completion path no longer publishes its own state, scenario, or research-run row before the claim-owned completion transaction.
However, a completed day still authorizes every same-day `portfolio.state` evidence row rather than one completion-owned evidence identity, so an orphan can become visible to `portfolio_state_asof`, `portfolio_report`, and the next rerun after an otherwise valid completion.
The parallel analyst sweep also introduces a high-severity invalidation-latch escape because each graph uses a child temporal context while only the parent context is checked before completion.

## Finding 1 — partially fixed, High residual

Original failure scenario: “Two overlapping scheduler or manual invocations can both pass the check and spend the full provider budget, while a crash after `record_portfolio_state` but before `seal_scenario` leaves an unsealed state that a retry consumes as its prior portfolio.”
Original failure scenario: “A crash after scenario sealing but before `record_research_run` instead creates a sealed day with no terminal experiment record and a rerun that refuses to repair it.”
Overlapping invocations can no longer both become owners because `portfolio_days.day` is the primary key and claim selection and insertion run under the mutator lock at `tradingagents/temporal/store.py:191-199` and `tradingagents/temporal/store.py:315-325`.
The production path does not write portfolio state before completion and instead calls the single completion method at `tradingagents/portfolio_run.py:378-385`.
That method verifies the active claim and inserts state evidence, scenario, research run, and completed status inside one SQLite connection context at `tradingagents/temporal/store.py:351-359`, so process death cannot commit only a prefix of those SQL mutations.
Artifacts are prepared before that transaction at `tradingagents/temporal/store.py:342-350`, but orphan artifact blobs and artifact catalog rows are not portfolio projections and cannot become prior state.
The residual is operationally High because a process death during provider work leaves status `claimed`, and `claim_portfolio_day` returns every non-failed row unchanged at `tradingagents/temporal/store.py:322-325` while the runner treats a foreign claim as a permanent skip at `tradingagents/portfolio_run.py:170-174`.
There is no lease expiry, owner heartbeat, or explicit recovery transition for an abandoned claim, so the day can remain unrerunnable forever even though no partial production output is visible.

## Finding 2 — still possible, High

Original failure scenario: “A process killed between lines 252 and 267 can therefore silently alter the next day's holdings and appear in the track record even though no immutable portfolio scenario exists.”
The exact production kill window is closed by the completion transaction at `tradingagents/temporal/store.py:351-359`.
The authoritative join is nevertheless still wrong because `portfolio_state_asof` and `portfolio_states` accept any evidence whose JSON day has any completed lifecycle row at `tradingagents/temporal/store.py:1583-1588` and `tradingagents/temporal/store.py:1603-1607`.
The lifecycle row stores neither a state evidence ID nor a scenario ID at `tradingagents/temporal/store.py:191-199`.
The still-public fixture helper can create an unowned same-day state at `tradingagents/portfolio_run.py:50-73`, and later completion of that day makes both the orphan and the completion-owned row visible.
A read-only temporary-store probe wrote one orphan state with equity `999`, then atomically completed the same day with equity `100`, after which `portfolio_states()` returned both rows, `portfolio_state_asof()` returned `999`, and `portfolio_report()` returned `100`.
The disagreement occurs because the as-of query chooses by evidence ID at `tradingagents/temporal/store.py:1587`, while the report overwrites duplicate days in iteration order at `tradingagents/portfolio_run.py:400-402`.
This is High integrity risk because prior holdings and the published report can disagree and a later rerun can consume the orphan as authoritative state.
The lifecycle must store the exact completion-owned state evidence ID and projections must join on that identity, not on the day string.

## Finding 4 — partially fixed, Medium residual

Original failure scenario: “At the observed 21:30 collision, the still-running portfolio graph continues to append LLM calls and retrieval traces while reindex deletes and recreates roughly 171,000 derived documents, so those writes wait behind the SQLite writer and can exceed the 180-second busy timeout.”
The destructive full-size transaction is gone because reindex pins an evidence high-water mark, builds a private generation in 500-row transactions, and performs only delta catch-up plus publication in the final transaction at `tradingagents/temporal/store.py:556-606`.
The canonical table renames and generation-state update are atomic inside that transaction at `tradingagents/temporal/store.py:610-619`, so readers cannot see a half-renamed published schema.
The original multi-hour writer blockade is therefore impossible unless the delta itself is unexpectedly huge.
The residual is Medium because delta catch-up also runs a whole-generation cluster refresh inside the publication write transaction at `tradingagents/temporal/store.py:600-608`, which can again hold the SQLite writer for a long interval when many rows arrive during the build.
Concurrent readers are not actually pinned to one generation because the retriever reads `generation_id`, corpus hash, index contents, filters, and hydrated rows through separate connections at `tradingagents/temporal/retriever.py:42-50`, `tradingagents/temporal/retriever.py:66-70`, and `tradingagents/temporal/retriever.py:102-111`.
A swap between those reads can label new-generation results with the old generation ID or combine a cached old index with newly published hydration, so generation identity under concurrent readers remains a regression gap.

## Finding 5 — still possible, High

Original failure scenario: “Every ticker may fail, every quote may be absent, or the run may outlive its operational window, yet the code still calls the CIO, writes an empty or partially marked state, seals the scenario, and exits successfully apart from informational failure text.”
Minimum analyst coverage, all-quotes-missing, held-position quote coverage, a call count, and fixed failure outcomes now gate completion at `tradingagents/portfolio_run.py:285-320`.
Those checks prevent the all-ticker-failed and all-quotes-absent variants from sealing.
The finding remains High because the deadline and call ceiling are in-memory pre-call checks only at `tradingagents/portfolio_run.py:248-257`, and `ThreadPoolExecutor` waits for already-started calls at `tradingagents/portfolio_run.py:275-283`.
A hung or very slow provider call can therefore exceed the 900-second deadline indefinitely, and there is still no provider timeout enforced here.
There is no durable pre-spend reservation and no token, currency-cost, retry, or per-model ceiling despite the checked-in policy containing only wall time, call count, coverage, workers, and held quotes.
Missing quotes for unheld researched tickers are silently removed from the CIO universe at `tradingagents/portfolio_run.py:294-325`, so complete tradable-universe quote coverage is not enforced.

## Finding 6 — fixed, with one related regression recorded under finding 8

Original failure scenario: “The portfolio has two decision layers today: each ticker pays for analysts, bull/bear debate, trader synthesis, risk debate, and a final decision, then a cross-sectional CIO call sizes the portfolio from only the final rating and the first 400 characters of that decision.”
Production sets `analysts_only` and disables checkpoint sharing at `tradingagents/portfolio_run.py:468-477`.
The graph builder terminates after the final analyst and never adds debate, trader, risk, or portfolio-manager nodes in that mode at `tradingagents/graph/setup.py:106-125`.
The sweep uses bounded four-way parallelism at `tradingagents/portfolio_run.py:275-283`.
Each executor thread lazily owns a distinct graph and recorder at `tradingagents/portfolio_run.py:484-491`, so no graph instance is concurrently shared.
Each ticker graph receives a tape identity derived from the day run and ticker at `tradingagents/portfolio_run.py:497-507`, while transactional LLM and tool sequence allocation is protected at `tradingagents/temporal/store.py:1095-1124` and `tradingagents/temporal/store.py:1399-1432`.
The original costly full-graph and serial-sweep scenario is therefore impossible for the production-day input path.

## Finding 8 — partially fixed, High parallel escape

Original failure scenario: “Both request and response persistence exceptions are only logged, so a transient lock timeout during the observed reindex collision lets provider work continue and the portfolio day seal with a truncated tape.”
Recorder failures now irreversibly invalidate the current temporal context and log fixed text without exception details at `tradingagents/temporal_adapters/langchain.py:299-333`.
The portfolio parent context is checked before completion at `tradingagents/portfolio_run.py:363`, and a parent invalidation is converted to a failed lifecycle row at `tradingagents/portfolio_run.py:180-182`.
That closes the sequential and CIO-context failure paths.
The parallel production sweep remains High risk because `research_one` installs the parent context at `tradingagents/portfolio_run.py:262-268`, but `research` creates a distinct worker context at `tradingagents/portfolio_run.py:497-505`, and `TradingAgentsGraph.propagate` installs that child context at `tradingagents/graph/trading_graph.py:427-430`.
A worker tape failure therefore latches only the child object, the graph never calls `ensure_valid` on that child before returning at `tradingagents/graph/trading_graph.py:467-485`, and the parent checked at `tradingagents/portfolio_run.py:363` remains valid.
If the analyst still returns a brief, the claim-owned completion can seal with a truncated worker tape, recreating the original evidence-integrity failure under the new parallel architecture.
The child latch must be checked and propagated to the claim-owned parent before accepting each worker result.

## Finding 10 — fixed

Original failure scenario: “A historical GDELT query performed today creates archive-reconstructed evidence that appears to have been locally observed at the old GDELT timestamp, allowing it into an earlier `as_of` corpus without retaining the actual retrieval time as `observed_at`.”
GDELT now records the fetch receipt as both `available_at` and `observed_at` at `tradingagents/temporal_collectors/gdelt.py:166-168`.
The provider `seendate` survives only as `provider_available_at_estimate` metadata and the policy is named `fetch-receipt` at `tradingagents/temporal_collectors/gdelt.py:156-163`.
The historical backdating scenario is therefore impossible through this importer.

## Regression summary

The analysts-only graph shape, bounded worker count, per-thread graph ownership, and per-ticker tape naming are correct.
The run-invalidation latch is correct for one context but does not cross the worker-child to day-parent context boundary.
The generation swap publishes atomically at the database level but the retriever does not hold a single read snapshot or otherwise pin all phases to the generation it reports.
The abandoned-claim lifecycle has no recovery policy, and completed-day projections authorize rows by day rather than by completion-owned identity.

## Verification

The focused selection `tests/test_portfolio_lifecycle.py tests/test_portfolio_run.py tests/test_temporal_langchain_adapter.py tests/test_temporal_documents.py tests/test_temporal_gdelt.py tests/test_temporal_gdelt_wayback.py` passed with 48 tests.
The duplicate-state adversarial probe used only a temporary local store and demonstrated two projected states plus divergent as-of and report results for one completed day.
No live store, provider, deployment, or network operation was used.
The temporary test environment created for the permitted pytest run was moved to Trash afterward, leaving the assigned worktree clean.
