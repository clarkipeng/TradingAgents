# What we are doing

Updated: 2026-09-08 04:38 UTC.

The delivered audit fixes are integrated and locally verified in London through `d51e422`. The full [33-finding audit plan](docs/audit-fix-plan-2026-09-06.md), including C1–C4, remains **in progress**. Nothing has been deployed. [What changed, and why](docs/audit-repairs-explained.md) explains the work in plain English.

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

Code corrections are `6055e8d` (production lifecycle and replay consumers), `5d2fce3` (concurrent cache and offline comparison), `d51e422` (fixture isolation), and `c9c0b6d` (deployment fixture). They follow the selectively integrated worker candidates through `0c157f3`. The user's uncommitted `AGENTS.md` and `taste.md` edits remain preserved.

## Remaining work and ownership

London owns the residual audit scope. The delivered worker assignments are complete; Terra, Sol, and Luna are idle, with branches and handoffs preserved. No worker currently has a new implementation assignment.

- **P0:** finish explicit scheduler dependency/readiness and missed-job policies, alert coverage, and operational delivery/recovery proof. Durable slots, a one-hour dispatch grace, and the new execution job do not establish every scheduling requirement. Expired pending decisions currently fail closed and block later research; recovery policy still needs an explicit disposition.
- **P3/P4:** finish stable captured-world membership and versioned snapshot/cluster semantics, remaining payload/retention migrations, cleanup followed by restore/replay proof, and representative concurrency/resource measurements. Pinned generations and additive indexes alone do not close these requirements.
- **P5:** produce fresh quality/cost evidence before changing model-visible prompts or enabling compaction. The offline comparison tool is measurement plumbing, not a quality result.
- **P6:** old-owner termination, complete ledger/evidence reconciliation, rollback, representative loaded operation, and any infrastructure consolidation remain operational gates. The deployment-wrapper test gate is now closed locally; production cutover is not complete.
- **Legacy records:** assess existing seals individually for replayability and temporal validity. No blanket validity claim or retrospective rewrite was made.

## Cost and wakeups

The work reused visible Sightmesh workers with separate file ownership. London corrected gaps at the actual consumers instead of commissioning another general review. No reliable per-category token or dollar totals are available for implementation, management, corrections, audits, or wakeups, so no numerical savings are claimed.

The 15-minute fallback remains loaded but suppresses model prompts while London owns the remaining work and workers have no assignment. Six local guard fixtures verify active-work, approval, review-pending, and root-owned behavior. Native terminal callbacks remain available. Automatic push into this Conductor chat has not been established; this is not a claim of continuous autonomous follow-up here. Sol archival remains held by the workspace service's unconfirmed detached-tool cleanup; repeated archive attempts have stopped and its source is preserved.

Exact session identities, reconciliation notes, wake state, and check logs are retained in the ignored `.context/audit-fleet/`. [taste.md](taste.md) records the recurring engineering and efficiency lessons at their source.
