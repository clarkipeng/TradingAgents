# Audit fix plan

Status: implementation in progress; current verified outcomes and remaining work are in [doing.md](../doing.md).
Baseline: `afbd64f` on `agent-trading-simulation-backtest`.
Sources: [the 33-finding audit](audit-2026-09-06.md), the attached review transcript, current code, and the existing engineering taste canon.
The packages below define the required completion gates; they are not claims of deployment or operational verification.

## Outcome and order

Make a completed trading day reproducible, temporally valid, affordable, and recoverable before simplifying its infrastructure.
Keep the existing modular monolith and SQLite store; improve the boundaries already in use.
Seven packages each end in an observable completion gate.

| Order | Package | Audit findings | Completion gate |
| --- | --- | --- | --- |
| P0 | Stop silent failures and preserve recoverability | 6, 9-14, 32-33 | Failed CLI run is nonzero; shutdown reaps children; restore works; no duplicate spend |
| P1 | Prove capture and replay use the same world | 1-3, 28; C1 below | Actual portfolio entrypoint replays all workers and CIO without external access |
| P2 | Make execution and budgets explicit | 4-5, 7-8; C2-C3 | Time-valid quotes, affordable orders, conserved cash, visible policy failures |
| P3 | Remove measured database work | 15-16, 19, 21-22, 25-26, 29-30 | Identical outputs on representative data, less work and shorter lock holds |
| P4 | Version retrieval and storage semantics | 20, 23-24, 31 | Old seals still verify; new snapshots stay stable; restore survives cleanup |
| P5 | Reduce provider and model waste | 17-18, 27; structured briefs | Vintage-safe cache parity; measured token savings with preserved research quality |
| P6 | Simplify deployment after proof | Postgres seam; C4 | One paid owner, reconciled ledgers, recoverable cutover, measured capacity headroom |

P0 comes first because an unobservable failure or unusable backup undermines every later rollout.
P1 and P2 establish the product's trust contract.
The additive indexes in P3 can land early after P0's backup gate; they need not wait for all replay work.
P4 depends on P1's manifest and P3's shortened transactions.
P5's cache work depends on P2's price policy, and its prompt changes depend on P1's replay versioning.
P6 waits for P0-P4 and representative operational evidence, not a named weekday.

## Corrections to the proposed fixes

The audit findings are useful; their suggested implementations are hypotheses, not instructions to copy literally.
Code-path observations were checked locally; timings, corpus counts, costs, and prior production outcomes remain measurements reported by the earlier audit, not fresh measurements from this pass.

- **A three-hour claim expiry is containment, not ownership.** `complete_portfolio_day` already rejects a superseded claim ID. The demonstrated risk is overlapping research and spend; the code does not establish that two claimants can both commit the same day. Keep that fence and prevent live-owner takeover.
- **Fly shutdown cannot wait two hours.** Its maximum grace is 300 seconds. Drain before planned replacement, and stop/checkpoint interrupted work within a smaller bounded window. [Fly lifecycle documentation](https://fly.io/docs/blueprints/long-running-tasks/)
- **Executor timeout is not process termination.** `shutdown(wait=False)` does not make unfinished threads disappear at exit. Use the supervised job process as the hard cancellation boundary. [Python 3.12 executor documentation](https://docs.python.org/3.12/library/concurrent.futures.html)
- **Do not fix replay with a wildcard and a global sort.** Concurrent ticker tapes need explicit membership and separate ordered streams. Assess existing days individually; neither full recoverability nor permanent impossibility has been established for every day.
- **Adjusted close is not automatically the right fill price.** Separate raw execution prices and corporate-action accounting from adjusted analytical series. Pin provider adjustment settings; the API exposes adjustment and action options. [yfinance documentation](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
- **A covering index does not make a range count constant-time.** Likewise, appending IDs to SHA-256 does not preserve a digest over sorted canonical IDs. Measure remaining work and preserve old digest bytes.
- **Do not delete needed readers.** `corpus_overview` needs its evidence join when filtering on `e.source`; `evidence_fts` has a benchmark consumer. Migrate these contracts before deleting dependencies.
- **Persistent connections need a lifecycle.** Scope them per thread, finish transactions, close explicitly, and measure WAL growth. Long readers can block checkpoint progress. [SQLite WAL documentation](https://www.sqlite.org/wal.html)
- **Tool history and retained tapes are not automatically waste.** Prior results can support later reasoning; old traces can be essential to replay. Compress model input under a versioned policy and retain evidence required by seals.

## P0: stop silent failures and preserve recoverability

Owners: `cloud_supervisor.py`, `portfolio_run.py`, lifecycle methods in `temporal/store.py`, CLI adapters, poller supervision, backup scripts, and the production runbook.
Deliver small changes for outcomes, backups, process ownership, and scheduling.

1. Return a typed day outcome and derive CLI exit status from it: completed/already completed succeeds; failed, interrupted, and ownership contention are distinguishable. Verify the durable day record before reporting completion. Use fixed error codes, never raw commands or exception payloads that could contain credentials.
2. Repair the single-slot backup reader immediately, then replace filename inference with a versioned manifest. Publish a consistent SQLite snapshot, referenced artifact hashes, and required capture/budget ledgers as one recoverable backup set. Verify integrity and hashes before atomic publication. Preserve the last valid backup until replacement succeeds. Budget disk for the database, old and incoming backups, WAL, artifacts, and migration scratch space.
3. Use an application-level execution lock for the portfolio chain, separate from short store mutation locks. The job process owns it for its lifetime. Keep the durable claim ID as the commit fence; reclaim only after exclusive ownership is acquired and the old process is gone. Cover different dates too, so a backfill cannot race the scheduled day over predecessor state. Do not delete claims as routine recovery.
4. Track each job's process group. On shutdown, stop dispatching, signal the active group, bound grace below Fly's maximum, kill if needed, and reap before permitting retry. Record interruption on recovery if the dying process could not do so. Add provider deadlines without assuming cooperative calls always return. Planned deployments drain first through the existing release path.
5. Replace the fired set and one-hour dispatch window with durable `(job, logical_date)` outcomes and explicit dependency rules. Restart resumes pending work and records missed work. Discovery and backup still run if the portfolio fails; required import/capture readiness gates research. Separate provider-cycle dates from exchange-session dates, including holidays, early closes, DST, and delayed execution after midnight.
6. Give the ticker poller explicit capabilities: sources, pause policy, macro allowance, health endpoint, and alert identity. A pause switch gates every source it claims to govern. Preserve provider cooldown and spend state across restarts. A failed post-cycle hook retries that hook, not the completed provider sweep.
7. Alert on unsealed expected days, overdue progress, unhealthy capture coverage, stale backups, and low disk. Use the existing alert mechanism with local test sinks; verify delivery and recovery during rollout. Notification configuration and liveness are separate facts.

**Before:** success inferred from process exit; old worker presumed dead by age; backup writer and reader disagree; late jobs disappear.
**After:** one live owner, durable outcomes, recoverable backup sets, explicit pending/failed jobs.
**Tradeoff:** a small job ledger and temporary disk overhead; interrupted days can still consume reserved spend.
**Gate:** simulate SIGTERM and a hung provider in child processes; overlap manual/scheduled/backfill runs; restart across midnight; fail discovery; restore on an empty directory with source access disabled.
A bad/missing artifact must prevent backup publication; a hook failure must cause zero repeated provider calls.

## P1: prove capture and replay use the same world

Owners: `production_day_inputs`, `TemporalContext`, `TemporalGateway`, `LangChainTapeRecorder`, `TapeChatModel`, graph setup, and the verified-market tool.
Extend existing scenario snapshots and artifacts instead of adding a second tape system.

Create a versioned manifest binding parent run, exact ticker child IDs, CIO stream, completed-call counts/digests, tool streams, resolved instrument context, graph/tool/prompt versions, policy, universe, and predecessor state.
Read each child stream in recorded sequence; do not order cross-worker calls by completion time.
Keep strict request matching and require complete tape consumption.
Seal only after all required streams/artifacts are complete and the claim is still owned.

Resolve mutable instrument context once through the temporal boundary and reuse its captured value in replay.
Route verified-market snapshots through `invoke_tool`, including errors and request normalization.
In analysts-only runs, omit unused memory lookup, outcome-resolution calls, and memory writes by construction; freeze memory an active node actually consumes.

```python
# Before: bare parent lookup and mutable context reconstruction.
tape = calls(run_id=seal.capture_run_id)
context = resolve_instrument_now(ticker)

# After: illustrative API built on existing scenario snapshots.
manifest = read_verified_manifest(seal)
for worker in manifest.workers:
    replay_graph(
        inputs=manifest.inputs_for(worker),
        llm=tape_for(worker.llm_stream),
        tools=tape_for(worker.tool_stream),
        external_access=False,
    )
replay_cio_and_execution(manifest)
assert_all_streams_consumed()
assert_result_matches_seal()
```

**C1, additional validity blocker:** `portfolio_run.py` sets a historical cutoff but always selects `LIVE_CAPTURE`; `gateway.py` stamps fresh responses with that virtual cutoff.
Backfilled research can consequently capture today's mutable inputs as though observed on the historical date.
Use distinct policies for forward collection, new research against historical evidence, and exact replay of an existing run.
Historical research may make new model calls with actual execution timestamps, but its data tools must use the frozen historical corpus and never fetch fresh data.
Forward capture records actual observation time; source-certified historical availability requires an explicit reconstruction policy and fidelity label.
Apply the same clock contract to errors.
For forward research too, finish required collection/import, select a decision cutoff that includes those actual observations, and pin that evidence snapshot before research starts.
A data-tool miss is explicit missing evidence; it cannot quietly fetch something newer into the frozen world.
Model computation may finish later, and the execution policy uses its actual decision-ready time.

**Gate:** exercise the actual portfolio composition with multiple tickers, all four analysts, CIO, quotes, and execution; replace only external boundaries with recorded fixtures.
Keep context resolution and verified-market tools active, deny external network access in replay, perturb worker completion order, inject missing tapes, and compare decisions, orders, fills, cash, and state digests.
Attempt historical research with fresh providers configured: data fetches must be rejected and responses observed after cutoff must remain invisible.

**Legacy treatment:** preserve existing seals and produce a separate per-day assessment: state-verifiable, replay-verified, missing-input, or temporally suspect.
Recover stream membership only when retained evidence establishes it unambiguously, with a documented reader version or separately hashed attestation.
Never disable prompt matching or fetch replacement inputs to pass a replay test.
New-policy experiments use separate scenario/strategy identities and cannot silently join the old return series.
**Tradeoff:** explicit manifests and legitimate legacy reader support; some days may remain unreplayable.
**Benefit:** replay becomes an observed outcome, with historical limitations visible.

## P2: make execution and budgets explicit

Owners: input adapters, CIO boundary, planner, simulator, and portfolio report.
Follow the existing `TargetPortfolio` direction in [ADR 0002](architecture/decisions/0002-target-portfolio-boundary.md); add only the exercised adapter needed here.

Return typed quotes with instrument, session, observation time, adjustment basis, price, and evidence ID.
Parse CSV properly, choose the latest eligible session independently of row order, and reject nonfinite/nonpositive or stale prices.
Isolate expected quote errors per symbol; held-position coverage remains mandatory and an unusable book fails explicitly.
Filter briefs and allowed allocations from the same eligible universe while retaining positions required for marking and risk checks.

**C2, execution timing:** the driver assigns cutoff, order submission, quote availability, and fill time to the same `21:30Z` instant even though scheduled research happens later and uses closing prices.
Retain that calculation for legacy verification, but do not present it as attainable forward execution.
Default the new policy to after-close research with pending targets filled at the next exchange session's eligible opening quote, applying opening gaps and corporate actions to held positions.
Separate decision and execution records, each claimed and immutable, and link pending targets to eventual fills.
The return series gets a new policy version.

Distinguish invalid proposal, model failure, budget exhaustion, deadline breach, lost ownership, and tape corruption.
Only an invalid proposal may take the documented hold-book fallback, labeled degraded with its cause.
Infrastructure and budget failures cannot seal a successful hold day.

**C3, actual spend limits:** `MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY` counts graph invocations, not the many model calls inside them.
Add a shared per-day monetary/token reservation ledger at the model boundary, including CIO headroom and retries.
Reconcile returned usage, retain reservations for ambiguous failures, and scope the ceiling across attempts.
Keep the graph-call guard as an honestly named workload limit.

Use a pinned local Decimal context and explicit currency/share rounding rules.
Share one execution-cost function between planning and simulation.
Apply sell decisions and the rebalance band first; calculate cash net of actual sells, fees, and slippage; then choose affordable whole-share buys using a documented deterministic residual-allocation rule.
Report target deviations from rounding/bands instead of silently dropping infeasible orders.

```python
# Before: nominal target dollars become orders; infeasible fills get skipped.
orders = whole_shares(target_weights * equity / close)

# After: planner and simulator share the same versioned cost policy.
with localcontext(execution_policy.decimal_context):
    sells = plan_sells(current_book, targets, execution_quotes, band)
    cash = simulate_sells(current_book, sells, execution_policy).cash
    buys = affordable_buys(targets, cash, execution_quotes, execution_policy)
    result = simulate(current_book, sells + buys, execution_policy)
    assert result.cash >= 0
    assert_cash_and_position_conservation(result)
```

**Gate:** both CSV row orders; split/dividend fixtures; holidays and opening gaps; optional versus held quote failure; CIO budget exhaustion; concurrent reservations; fees with a suppressed sell; cash-only/fully-invested targets; repeated days under an altered global Decimal context.
**Tradeoff:** targets wait for eligible execution quotes, residual cash remains, and comparisons across policy versions need labels.
**Benefit:** every simulated trade has an explicit timing and accounting contract.

## P3: remove measured database work without changing answers

Owners: store, retriever, adapters, and CLI composition.
Start with additive migrations and a benchmark before each change.

- Add cluster lookup indexes to current and future generation tables and an evidence availability index suited to the actual query.
- Remove unused full-payload rereads, preserving return contracts for callers that need records. Batch a tool call's evidence references and trace publication in a short transaction; allocate sequence ranges atomically per stream where needed. Keep paid-request reservations and LLM begin records durable before external calls.
- Start with a partial expression index for media source/ticker/availability; add generated columns only if measured plans need them. Aggregate corpus-overview counts and spans in SQL while retaining source-filter behavior.
- Reuse connections only within an owned thread lifecycle, with no transaction open between calls. Resolve generation identity once per search and validate at publication boundaries.
- Move HTTP and bulk index construction outside the global store lock, including outer CLI locks. Use bounded batches, a source watermark, and catch-up/revalidation before atomic generation swap.
- Delete unreachable retrieval code and exact duplicate indexes after checking callers, uniqueness, column order, collation, and partial predicates.

**Gate:** old/new result, ranking, trace, and digest equivalence; intended query plans on representative data; concurrent capture/research/reindex with measured writer progress; failure halfway through batch publication.
Measure cold/warm latency, transactions/connections per call, rows visited, lock duration, memory, WAL, and file size.
Require less observed work without a correctness/resource regression; do not infer wall-clock savings by adding overlapping timings.
**Tradeoff:** index storage and migration I/O; WAL does not cure long transactions.

## P4: version retrieval and storage semantics

Owners: corpus identity, generation publication, scenario readers, artifacts, and retention tooling.

```python
# Before: our own recordings mutate the hash used by an ongoing search.
world_hash = hash(all_evidence_at(cutoff))

# After: world membership and execution records have separate identities.
snapshot = pin_world(cutoff, evidence_ids, retrieval_generation, policy_version)
page = search(snapshot, query, cursor)
append_run_tape(run_id, page)  # does not change snapshot membership
```

Pin captured-world evidence membership and retrieval generation per run.
Keep run-generated tapes in its execution manifest instead of changing the world's identity.
For old seals, stream the exact original canonical JSON into the hash to lower memory while preserving digest bytes.
For new seals, version the snapshot definition; a newly eligible document creates a new snapshot, not a mid-pagination mutation.

Build cluster candidates from indexed postings outside the publication lock.
Version a candidate policy that stays stable within its pinned generation.
New articles can legitimately bridge clusters: old replay uses its original generation, and new research uses an updated one.
Prove incremental/full rebuild equivalence over append sequences, corpus-size thresholds, repeated titles, and bridges, not just one fixed corpus.

Retire `evidence_fts` after migrating readers and a rollback window; deleting it does not necessarily shrink the database immediately.
Compact on a verified copy with sufficient scratch space.
Move duplicate LLM payloads into hash-addressed artifacts with verified backfill and a compatible reader before removing inline copies.
Retention follows reachability from retained seals, runs, evidence, and operational ledgers, not `DELETE WHERE age > N`.
Archive pinned data with a restore contract; reclaim only unreferenced artifacts, disposable generations, and expired unsealed debug data under a written policy.
Dry runs report counts/digests; backup publication protects its referenced artifacts during copying and cleanup.

**Gate:** old-seal verification/replay unchanged; tapes do not invalidate a pinned world; pagination survives ingestion; generated corpora rebuild identically; restored archives replay after cleanup.
**Tradeoff:** retained generation/digest readers and migration space.
**Benefit:** cheaper storage and stable inputs without erasing evidence.

## P5: reduce provider and model waste

Owners: market-data loader, prompt composition, tool-message handling, and brief construction.

Share OHLCV loading/indicator computation and reuse instrument profiles within a run.
Keys include provider, instrument identity, date range, adjustment policy, and captured vintage.
A current adjusted five-year file is not historical truth simply because it contains old dates.
Bound cache memory and concurrent builds; tape the exact delivered response.

Place stable instructions before ticker context, with prompt versions in the manifest.
Replace JSON slicing with deterministic per-analyst sections and token budgets, preserving dates, caveats, and citations.
Start without an extra summarizer call.
Batch compatible indicators before shortening message history.
Compact old tool messages only after comparing final reports and allocations on a frozen corpus; preserve tool-call/result pairing, needed facts, errors, and citations.
Original tapes stay complete even when the model sees a shorter derived view.

**Gate:** cache/uncached parity for the same vintage; changed vintage misses; malformed optional fields leave healthy records usable.
Old prompts replay exactly; compare new prompts/compaction on fixed scenarios for coverage, factual errors, citation grounding, allocation validity, and measured cost.
Set quality tolerances before that comparison.
An old model tape cannot prove a new prompt's reasoning quality; fresh-model comparisons use a separate bounded experiment budget.
**Tradeoff:** evaluation cost and possible quality loss from compaction.
**Benefit:** fewer downloads and tokens, measured at whole-day level.

## P6: simplify deployment after proof

Keep Postgres and the separate collector until the cheaper topology passes restore, interrupted-day recovery, and replay checks.
Consolidation means one paid capture owner on the trader host, separated from research by process lifecycle and budget ledger.
Use the existing media-store interface; no distributed queue or collector-schema rewrite is needed.
Remove the networked import seam only after data, receipts, leases, reservations, cycle state, and provenance reconcile.

At a clean UTC provider-cycle boundary: stop the old paid owner; confirm termination/lease release; export the final ledger/evidence; import idempotently; compare IDs/counts/digests and spent reservations; start the new owner; verify read-only receipts.
Rollback stops the new owner before enabling the old and transfers intervening spend/capture state back.
An empty copied ledger is never a paid-retry mechanism.
Keep a restorable archive and observation window before decommissioning Postgres.

**C4, measurement correction:** `RUSAGE_CHILDREN.ru_maxrss` is a cumulative child high-water statistic, not reliable per-job telemetry.
Measure each job's process-tree/cgroup peak plus whole-machine memory, disk/WAL, and CPU contention before rightsizing.
Include concurrent capture, research, backups, and generation publication.
Shrink one resource at a time only when measured headroom and deadlines survive.
Do not change the chosen X coverage tier incidentally.

**Benefit:** fewer moving parts and potentially lower recurring cost.
**Tradeoff:** capture and research share a failure domain; resource pressure can threaten irreplaceable capture.
One clean trading day is insufficient for this cutover: require representative loaded operation and a complete scheduled chain with verified backup and collector coverage.

## Coverage ledger: all 33 findings

IDs follow the durable audit, not the shortened chat summary.
This ledger maps required proof to every finding. Package completion is recorded in [doing.md](../doing.md), separately from individual passing checks.

| ID | Finding | Package | Required proof |
| --- | --- | --- | --- |
| 1 | Child research tapes unreachable | P1 | Exact child streams and CIO resolved |
| 2 | Capture/replay context diverges | P1 | Captured context and verified prompts match |
| 3 | Verified-market tool bypasses tape | P1 | Full-tool replay with network disabled |
| 4 | One quote exception aborts day | P2 | Optional failure isolated; held quote required |
| 5 | Row order, date, price basis | P2 | Session/adjustment fixtures pass both orders |
| 6 | Claim expires during healthy run | P0 | Concurrent attempts cannot work under two owners |
| 7 | CIO universe and budget outcomes | P2 | Matching briefs/universe; explicit budget failure |
| 8 | Costs, rounding, infeasible buys | P2 | Planner/simulator parity and cash conservation |
| 9 | Signals, deadlines, hung threads | P0 | Group exits and is reaped within bounded grace |
| 10 | Failed day exits successfully | P0 | Durable status, exit, and alert agree |
| 11 | Wrong discovery date | P0 | Explicit logical date across midnight and DST |
| 12 | Backup reader uses old filenames | P0 | Producer-to-reader-to-restore contract |
| 13 | Poller inherits wrong capabilities | P0 | Sources, pause, health, alerts explicitly scoped |
| 14 | Post-cycle errors repeat fetches | P0 | Hook retry never repeats completed provider stages |
| 15 | Missing cluster index | P3 | Query plan and generation creation include index |
| 16 | Missing availability index | P3 | Wide reads reduced; remaining scan measured |
| 17 | Price, indicator, profile cache waste | P5 | Vintage parity and fewer actual provider calls |
| 18 | Repeated tool results | P5 | Fewer tokens with quality gates met |
| 19 | Tape rereads and transaction churn | P3 | Atomic ordered traces with fewer transactions |
| 20 | Hash cost and self-invalidation | P4 | Legacy digests match; pinned world stable |
| 21 | Media JSON scans | P3 | Index preserves cutoff, order, limit |
| 22 | Corpus overview aggregation | P3 | SQL retains source-filter semantics |
| 23 | Redundant evidence FTS | P4 | Readers migrated; restore/search pass |
| 24 | Cluster cost and instability | P4 | Incremental/full parity; pinned generation |
| 25 | Connection/generation lookup churn | P3 | Bounded owned connections; no lingering readers |
| 26 | Outer locks cover HTTP/rebuild | P3 | CLI permits writer progress during slow work |
| 27 | Prompt prefix defeats caching | P5 | Versioned prompts; measured cache usage |
| 28 | Unused analysts-only memory | P1 | No unused memory work or hidden model calls |
| 29 | Unreachable search implementation | P3 | One live implementation; callers pass |
| 30 | Duplicate trace indexes | P3 | Constraints survive; write cost measured |
| 31 | Unbounded traces/duplicate payloads | P4 | Reachability dry run; retained days restore/replay |
| 32 | Python floor/TimeoutError mismatch | P0 | Catch futures exception on supported 3.10 and 3.12 |
| 33 | Fired-set pruning | P0 | Durable scheduling replaces set; bounded retention |

Earlier suspicious code is also covered: Postgres removal in P6; lost fixed-time slots in P0; self-changing corpus identity in P4; brief slicing in P5; claim takeover in P0.
Operational follow-ups include artifact backups, stall alerts, failure notifications, and rightsizing.
C1-C4 are additional code-backed concerns, not silently folded into the original count.

## Recurring mistakes and prevention

| Repeated pattern | Why the local fix missed it | Prevention |
| --- | --- | --- |
| “Replayable” based on tape counts | Producer success substituted for consumer proof | Actual portfolio replay with external access denied |
| Green tests, live schema failures | Fixtures copied assumptions; optional fields overvalidated | Recorded boundary fixtures plus malformed/missing-field cases |
| Another timeout increase | Tuning hid ownership, locks, caches, and scaling | Representative measurements plus ownership/cancellation invariants |
| Rename breaks download; disabled job returns | Only writer/current process changed | Consumer contract and persistent restart checks |
| Retries spend again; health reports success | Outcome, budget, and retry have different owners | Durable outcomes/reservations across attempts |
| Fast inner method, slow real job | Outer locks and concurrency omitted | Public CLI benchmark under capture/research contention |
| Cleanup threatens replay/reasoning | Redundancy inferred without checking consumers | Reachability audit, versioned views, parity/quality evidence |
| Repeated “all fixed”; contradictory status | Plans reported as observations | One status with build, gate, outcome, and uncertainty |

Durable prevention rules live in [taste.md](../taste.md), linked from `AGENTS.md` so future work encounters them.

## Delivery and verification

Keep one integration owner and one change per coherent contract.
Portfolio/store/supervisor edits overlap; do not assign simultaneous writers to the same files.
The user requested parallel implementation through Sightmesh: use one lead with bounded, non-overlapping owners and reuse existing workers when useful.
Each change records its invariant, focused checks, outcome, and rollback condition; update this ledger and `doing.md` together.

Use fixture-backed integration, fault injection, and representative stored data first.
Extend existing portfolio lifecycle/run/simulation, full-trace replay, LangChain adapter, archive replay, cloud supervisor, and retrieval benchmark suites at their missing boundaries.
Run the required integration suite on the combined code; repeat only for changes or unresolved failures.

Deploy from a clean committed tree through `scripts/deploy_collector.sh`, preserving preflight, health gate, and rollback.
Verify running build identity, durable job outcomes, complete tapes, collector receipts, and restored backups.
Diagnose through stored evidence; any necessary paid validation is a named bounded run in the existing ledger, never an untracked retry.
Passing local tests does not establish deployment or operational success.

Completion requires proof for every original finding, external-data-free replay, explicit temporal/execution policies, an honest old-data assessment, and scheduled recovery without repeated paid work.
Report performance and cost savings from comparable whole-day measurements.
