# What we are doing

Updated: 2026-10-18.

The delivered audit fixes are integrated and locally verified in London through `d51e422`. The full [33-finding audit plan](docs/audit-fix-plan-2026-09-06.md), including C1–C4, is **paused at the user’s request**; unfinished work is preserved below. Nothing has been deployed. [What changed, and why](docs/audit-repairs-explained.md) explains the work in plain English.

## Archival request

The user's 2026-10-18 request to preserve all TradingAgents work before deleting local checkouts has been carried out in the private [recovery archive](https://github.com/clarkipeng/TradingAgents-archive-2026-10-18). GitHub Actions are disabled. All 43 original/current local branches (including the canonical WIP snapshot), 16 tags, and 7 remote-tracking tips were uploaded; the index also preserves 421 historical checkpoint refs and eight checkout-head records in a Git bundle. A fresh GitHub clone passed Git integrity checks, all 281 artifact digests matched, and five local research databases passed restored integrity checks. The original bundle was restored and its refs/commit objects verified. Implementation remains paused.

The archive preserves London's documentation edits, the canonical checkout's unfinished source, worker handoffs, review artifacts, attachments, and local research data without merging unfinished experiments. Credentials and original local artifacts are separately preserved in `~/Documents/TradingAgents-archive-2026-10-18/local-only/`, outside the repository; retain that directory when removing local checkouts. Plaintext credentials were not uploaded. Remote cloud stores remain untouched.

Sightmesh now has 24 of the 26 TradingAgents workspaces archived (14 newly archived). Its service still refuses archival of `ta-sol-execution-v2` and `ta-luna-p4-resume` because detached-tool cleanup is unconfirmed. Their work is already in the verified archive; no execution process was running at inventory. No repeated archive attempts or implementation wakeups were issued. Calgary is already archived in Conductor. London requires the user's Archive action in Conductor because its supported local-workspace controls are user-only. No local repository was deleted.

See the archive's `README.md`, ref manifests, verification record, and workspace retirement report for recovery details and exact identities. The archive index is the final preservation record; the September verification below describes historical engineering checks, not new test runs.

## Disable request

The user requested disabling TradingAgents on 2026-09-15. Local audit wakeup `io.tradingagents.audit-fleet-wake-20260906` is disabled in launchd, unloaded, and removed from the startup folder. Its definition is preserved under `~/Library/LaunchAgents.disabled/tradingagents-20260915/`. The audit continuation state prevents further automatic work.

Terra, Sol, and Luna have no active execution processes. No local TradingAgents trading/collector process, TradingAgents crontab entry, or matching Repowire scheduled message was found. Code, stored evidence, and worker history remain preserved.

**Cloud shutdown is complete and verified (2026-09-16).** After interactive Fly sign-in, both deployed process groups in `tradagent` were explicitly scaled to zero: `app` (collector) and `trader` (research/supervisor). `fly status` and the Machines API now show zero Machines. The original `temporal_store` volume is preserved and detached; Postgres was not changed or deleted. No cloud worker remains to generate new collector alerts, though already queued mail may still arrive.

The first unqualified scale-to-zero command affected only the default `app` group. The `trader` group was then stopped explicitly, and the production-runbook pause commands now name both groups. The shutdown record contains sanitized before/after inventories in `.context/disable-tradingagents/`.

The checks below were completed on 2026-09-08; they were not rerun for this operational pause.

## Integrated and verified locally

- **Decision and execution lifecycle:** the CLI publishes an immutable pending target; the supervisor executes the queue at 09:31 New York time on exchange sessions. Readiness selects the first subsequent exchange open. Raw Yahoo opening data is fetched with explicit adjustment and action options. Response availability is recorded after the fetch. The chain lock, execution claim, and predecessor comparison prevent duplicate or stale-book execution; interrupted claims recover under exclusive ownership. Pending records and their tapes are retention roots. Before-open, expired-session, missing-quote, and changed-predecessor outcomes fail visibly.
- **Actual production replay:** two tickers, all four analysts, CIO, prices, fees, fills, cash, and final state replay through the real graph with model/data/context providers and sockets disabled. Both legacy forward capture and the new next-open path pass. The new path also passes with frozen historical inputs and a cutoff after UTC midnight. Changed reports, changed quotes, and deleted model calls fail. Explicit missing-input recordings replay as missing even after later ingestion; a null/corrupt legacy reference remains a failure. Historical legacy days still require individual assessment.
- **Time and reporting:** the logical trading day survives UTC midnight independently of the cutoff. Frozen instrument context is retained. Reports label the execution policy and valuation date and refuse aggregate returns across mixed policies.
- **Spend and failures:** real synchronous/asynchronous LangChain admission rejects a denied call before invoking the provider. Shared durable daily reservations cover output allowances, CIO headroom, retries, returned usage, and ambiguous attempts. Provider/CIO failure cannot become a successfully sealed fallback. Policy-valued cost is distinct from provider billing.
- **Storage and recovery:** generation publication rolls back atomically and failed builds can retry. Runs carry a pinned retrieval generation. Backups and mirror restore include the temporal database, referenced artifacts, and model-budget ledger, preserve the previous valid unit on corruption, and refuse an active writer. These local invariants do not establish completion of the broader P4 package.
- **Cost-preserving optimization:** identical adjusted Yahoo requests share one fetch within a run, including concurrent callers; failed/empty fetches can retry and a new run fetches a new observation. The brief-comparison command remains offline, refuses an absent tokenizer cache before network access, and leaves model-quality evaluation pending. Runtime prompt compression remains disabled.

## Verification

At the final combined source:

- **1,782 tests and 71 subtests passed** in the service-independent suite, excluding the separately checked deployment wrapper.
- **102 deployment-wrapper tests passed** with their original timeouts. The fixture now avoids starting two Python interpreters for each fake transport command; no deployment assertion or timeout was weakened.
- **Repository-wide Ruff passes.** One optional `langchain_aws` test was skipped and 14 service-backed tests were excluded.

The first combined broad run exposed a CLI source-selection fixture reaching its independent shadow collector. That fixture is isolated; the corrected broad run above passes. Earlier focused results are superseded by this combined proof. No production deployment, paid model-quality experiment, or infrastructure cutover was performed.

Code corrections are `6055e8d` (production lifecycle and replay consumers), `5d2fce3` (concurrent cache and offline comparison), `d51e422` (fixture isolation), and `c9c0b6d` (deployment fixture). They follow the selectively integrated worker candidates through `0c157f3`. The user's `AGENTS.md` and `taste.md` edits are included in the archival checkpoint.

## Remaining work and ownership

London owns the residual audit scope. The delivered worker assignments are complete; Terra, Sol, and Luna are idle, with branches and handoffs preserved. No worker currently has a new implementation assignment.

- **P0:** finish explicit scheduler dependency/readiness and missed-job policies, alert coverage, and operational delivery/recovery proof. Durable slots, a one-hour dispatch grace, and the new execution job do not establish every scheduling requirement. Expired pending decisions currently fail closed and block later research; recovery policy still needs an explicit disposition.
- **P3/P4:** finish stable captured-world membership and versioned snapshot/cluster semantics, remaining payload/retention migrations, cleanup followed by restore/replay proof, and representative concurrency/resource measurements. Pinned generations and additive indexes alone do not close these requirements.
- **P5:** produce fresh quality/cost evidence before changing model-visible prompts or enabling compaction. The offline comparison tool is measurement plumbing, not a quality result.
- **P6:** old-owner termination, complete ledger/evidence reconciliation, rollback, representative loaded operation, and any infrastructure consolidation remain operational gates. The deployment-wrapper test gate is now closed locally; production cutover is not complete.
- **Legacy records:** assess existing seals individually for replayability and temporal validity. No blanket validity claim or retrospective rewrite was made.

## Cost and wakeups

The work reused visible Sightmesh workers with separate file ownership. London corrected gaps at the actual consumers instead of commissioning another general review. No reliable per-category token or dollar totals are available for implementation, management, corrections, audits, or wakeups, so no numerical savings are claimed.

The 15-minute fallback is now disabled and unloaded per the user’s request. Its definition and prior six-fixture validation are preserved. No automatic audit continuation is intended while the project is paused. Sol execution and Luna P4 archival remain held by the workspace service's unconfirmed detached-tool cleanup; their source and handoffs are preserved in the verified private archive.

Exact session identities, reconciliation notes, wake state, and check logs are retained in the ignored `.context/audit-fleet/`. [taste.md](taste.md) records the recurring engineering and efficiency lessons at their source.
