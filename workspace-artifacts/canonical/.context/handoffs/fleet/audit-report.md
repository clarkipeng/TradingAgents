# TradingAgents audit at `1a70f38`

## Ranked findings

### Critical

1. `tradingagents/portfolio_run.py:187-273` does not give one owner atomic custody of a portfolio day.
The violated invariants are one owner per lifecycle, sealed-scenario immutability, and fail-closed persistence.
The initial `get_scenario` check is only a read, the expensive sweep runs without a day reservation or the store's mutator lock, portfolio state is written before the scenario is sealed, and the research-run record is written after sealing.
Two overlapping scheduler or manual invocations can both pass the check and spend the full provider budget, while a crash after `record_portfolio_state` but before `seal_scenario` leaves an unsealed state that a retry consumes as its prior portfolio.
A crash after scenario sealing but before `record_research_run` instead creates a sealed day with no terminal experiment record and a rerun that refuses to repair it.
The smallest robust fix is a durable portfolio-day lifecycle row with an atomic `claim -> complete` transition, a unique day identity, and one short final transaction that commits the state, scenario, and research-run linkage together; failed claims must remain explicitly resumable or terminal without becoming prior state.

2. `tradingagents/portfolio_run.py:53-81` and `tradingagents/portfolio_run.py:294-315` treat every `portfolio.state` evidence row as sealed even though sealing is a separate later operation.
The violated invariants are derived views over authoritative state and sealed-scenario immutability.
`portfolio_state_asof` can select an orphan written by a failed day, and `portfolio_report` explicitly says it reads only sealed state while querying no scenario table and resolving duplicate days with `last write wins`.
A process killed between lines 252 and 267 can therefore silently alter the next day's holdings and appear in the track record even though no immutable portfolio scenario exists.
The smallest robust fix is to make the sealed scenario snapshot or completed portfolio-day lifecycle record authoritative and derive prior state and reports only through that completed identity, with a foreign key or stored state evidence ID rather than a tool-name scan.

### High

3. `scripts/plan_a_experiment.py:108-112` does not enforce the experiment's corpus-integrity precondition.
The violated invariant is fail-closed validation at the experiment boundary.
`verify_scenario_corpus` returns a boolean, but the driver discards it, so a drifted scenario runs and spends real money despite the comment claiming it will refuse drift up front.
Late evidence whose `available_at` precedes the scenario cutoff is enough to make the check false and contaminate every arm while still producing apparently valid run IDs and comparison commands.
The smallest robust fix is `if not store.verify_scenario_corpus(scenario_id): raise/exit`, covered by a test named for refusing a drifted corpus before constructing or invoking a model.

4. `tradingagents/temporal/store.py:472-482` and `cli/main.py:1319-1329` hold both the advisory single-mutator lock and one SQLite write transaction across the entire destructive document rebuild.
The violated invariants are one owner per lifecycle and availability of the capture path.
At the observed 21:30 collision, the still-running portfolio graph continues to append LLM calls and retrieval traces while reindex deletes and recreates roughly 171,000 derived documents, so those writes wait behind the SQLite writer and can exceed the 180-second busy timeout; the advisory lock does not protect portfolio tape writes because they do not take it.
Chunking the current delete-and-repopulate transaction would be incorrect because readers could observe a partially empty or partially rebuilt index and because an interleaved new document could be lost from the rebuilt derivative layer.
The immediate smallest fix is to move reindex to a scheduler dependency after portfolio completion, with a generous quiet window and an explicit no-active-writer preflight, rather than merely choosing another fixed clock time.
The robust scaling fix is to build versioned shadow document and FTS tables from a pinned evidence high-water mark in bounded transactions, catch up the delta, then perform a short atomic generation swap; readers pin one complete generation and ingestion never waits for the full rebuild.

5. `tradingagents/portfolio_run.py:200-240`, `tradingagents/portfolio_run.py:252-290`, and `scripts/run_portfolio_day.sh:27-30` have no fail-closed coverage, quote, latency, or spend contract.
The violated invariants are fail-closed schema boundaries and durable budget receipts.
Every ticker may fail, every quote may be absent, or the run may outlive its operational window, yet the code still calls the CIO, writes an empty or partially marked state, seals the scenario, and exits successfully apart from informational failure text.
Tonight's observed 4.5-hour sequential run also demonstrates that the nominal daily job has no bounded completion time and no declared maximum number of model calls, tokens, dollars, retries, or simultaneous requests.
The smallest robust fix is a checked-in run policy with a hard per-day reservation before the first LLM call, per-model call/token/cost ceilings, a wall-clock deadline, bounded retry policy, minimum analyst coverage, and complete quote coverage for every held or tradable symbol; breach must terminate the day unsealed and emit a sanitized fixed-vocabulary outcome.

6. `tradingagents/portfolio_run.py:364-420` uses a complete debate-and-risk trading graph as the per-ticker feature extractor and executes all 30 tickers serially.
The violated taste rule is smallest robust architecture.
The portfolio has two decision layers today: each ticker pays for analysts, bull/bear debate, trader synthesis, risk debate, and a final decision, then a cross-sectional CIO call sizes the portfolio from only the final rating and the first 400 characters of that decision.
This duplicates portfolio judgment inside every ticker, discards most of the expensive output, and turns the observed roughly nine minutes per ticker into a 4.5-hour critical path.
The sweep should be analysts-only, producing a validated compact per-ticker evidence brief and deterministic features, because the single CIO is the owner of cross-sectional judgment and risk constraints.
The sweep can then run with bounded parallelism, initially three to five tickers, provided each worker has an independent graph instance and tape identity and the store's sequence allocation remains transactional; one shared `TradingAgentsGraph` instance should not be assumed thread-safe.
The operational contract should specify a p95 ticker deadline, an overall deadline comfortably before the reindex dependency, a fixed concurrency cap, and hard reserved call/token/dollar envelopes, with measured receipts reported by stage rather than a model name standing in for cost.

### Medium

7. `cli/main.py:1564-1567` crashes on the intended idempotent rerun path.
The violated invariant is tests as executable behavior at the user-visible boundary.
`run_portfolio_day` returns a skip object without a `failures` key for an already sealed day, but the CLI unconditionally indexes `summary["failures"]`.
A launchd refire correctly avoids provider spend and then terminates with `KeyError`, turning a healthy no-op into a failed scheduled job.
The smallest robust fix is one stable discriminated result schema for completed and skipped outcomes, followed by a CLI test that invokes the already-sealed path.

8. `tradingagents/temporal_adapters/langchain.py:299-328` silently degrades the advertised complete LLM tape when persistence fails.
The violated invariants are fail-closed experiment evidence and sanitized failure boundaries.
Both request and response persistence exceptions are only logged, so a transient lock timeout during the observed reindex collision lets provider work continue and the portfolio day seal with a truncated tape.
This defeats exact replay and can also log exception text from lower layers without a fixed sanitized vocabulary.
The smallest robust fix is a recorder health latch owned by the temporal run: any tape write failure marks the run invalid, prevents scenario completion, and surfaces only a sanitized error class while retaining local diagnostic detail in a non-secret controlled record.

9. `scripts/verify_x_cycle.py:33-35` and `scripts/verify_x_cycle.py:62-147` bind all X receipt and roster checks to the default local SQLite media store even though the X collector's authority is cloud Postgres.
The violated invariant is one authoritative representation with derived verification views.
In the deployed topology a healthy cloud X cycle can be reported missing locally, while unrelated or stale local rows can satisfy budget and coverage checks; the temporal mirror check is then joined to the wrong fetch-run inventory.
The smallest robust fix is a required or explicit `--media-db-url` pointing at the authoritative store, with backend-neutral read-only queries and a refusal when the configured target is not the X authority.

10. `tradingagents/temporal_collectors/gdelt.py:133-166` assigns `observed_at` and `available_at` from GDELT's article `seendate` rather than from the collector receipt.
The violated invariant is explicit availability clocks.
A historical GDELT query performed today creates archive-reconstructed evidence that appears to have been locally observed at the old GDELT timestamp, allowing it into an earlier `as_of` corpus without retaining the actual retrieval time as `observed_at`.
This may be an intentional archive reconstruction policy, but the current fields collapse provider visibility and local observation despite the rest of the system defining availability as when the system observed evidence.
The smallest robust fix is to retain the real fetch receipt as `observed_at`, name GDELT `seendate` as a separate provider-availability estimate, and make scenario policy explicitly choose whether archive-reconstructed estimates are eligible rather than overloading the forward-capture clock.

## New insights

- The portfolio sweep is not merely slow; its information contract is mismatched to its consumer.
The CIO receives a lossy 400-character tail product from a costly full decision graph, so optimizing prompts or choosing a smaller model cannot resolve the architectural waste.

- Bounded ticker parallelism is useful only after tape completeness becomes a completion gate.
Parallelism will increase short SQLite write bursts from LLM and retrieval traces, making the current log-and-continue recorder behavior and the reindex collision more frequent rather than merely exposing an existing nuisance.

- A fixed later reindex slot is not a durable scheduling solution because portfolio duration varies with provider latency, retry behavior, ticker count, and model configuration.
The correct scheduling abstraction is a dependency or lease: reindex begins only after the portfolio lifecycle is terminal and no capture writer owns the store.

- Reindex generation swapping would also improve reproducibility, not only uptime.
Recording the document-index generation alongside each search trace would make clustering and ranking inputs explicit and let a replay pin the exact derived view even after later reindexes.

- The current portfolio result measures equity without recording coverage-adjusted confidence.
A day with 29 successful tickers and a day with 3 successful tickers can both seal and appear adjacent in the same return series, so future evaluation should report eligible-universe coverage, evidence freshness, quote coverage, model spend, and elapsed time as first-class day metrics.

- Cost should be contracted in provider-independent units and reconciled to tape receipts.
A model alias can change pricing or token behavior, while counts of accepted input tokens, cached input tokens, output tokens, calls, retries, and computed currency cost preserve an auditable latency/cost frontier.

- The experiment chain has strong immutable pieces but no single terminal proof object.
Scenario identity, corpus hash, rubric, LLM tape, retrieval traces, research run, arm mapping, and score are persisted in separate places, including a mutable workspace JSONL file, so a compact run manifest hashing all of them would make completeness and comparison eligibility decidable by construction.

## Verification performed

The audit inspected the assigned implementation and focused tests at Git base `1a70f38` using read-only commands.
No provider-fetching commands, deployments, live-store writes, or repository mutations were performed.
