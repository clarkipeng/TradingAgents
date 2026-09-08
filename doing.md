# What we are doing

Updated: 2026-09-07.

Implementing the [audit fix plan](docs/audit-fix-plan-2026-09-06.md). All 33 findings and C1–C4 remain in scope; implementation, local proof, and operational proof are distinct.

## Verified outcomes

The first Sightmesh integration branch, through `9d9ae25`, is merged into this workspace. It includes operations/process cleanup, database improvements, explicit replay streams, captured contexts, and retention reporting. Sol reviewed the earlier candidate; its four findings received corrective changes. The last combined suite on that earlier candidate passed 211 tests. Subsequent changes carry focused proof and still need the next combined gate.

The model-budget boundary now prevents provider calls after admission fails, includes a completion allowance, settles returned usage, retains ambiguous attempts, and supports CIO headroom. Focused ledger/callback checks passed 28 tests, including real synchronous and asynchronous LangChain invocations; the expanded invocation suite passed 7 tests, including separate reservations for retries. Budget cost is valued by configured policy, not a provider invoice.

No production deployment or provider diagnosis has been performed. Prior sealed days still require their individual replay and temporal-validity assessments.

## Active ownership and remaining work

London owns current integration, the budget boundary, and this status. Terra owns production portfolio/replay/execution. Storage and operations successors own generation/retention/locks and backup/restore/cutover tooling when their launch is verified. Failed launch attempts are not active workers.

The remaining gates include actual portfolio replay, historical data isolation, versioned next-session fills, chain ownership, complete backup restore with the spend ledger, pinned retrieval generations, measured query parity, and prompt/cache quality. Model-visible optimizations and infrastructure consolidation stay behind their documented evidence gates.

## Cost and wakeups

Use at most two bounded implementation children under Terra. Reuse context, send short messages for meaningful changes, and run focused checks before one combined integration gate. Account for management, correction, review, and wake turns; cached/cumulative runtime counters do not establish dollar cost.

Native child terminal events wake Terra. A task-specific 15-minute fallback prompts only when known workers are idle and retires at completion or a real external gate. Each continuation must produce a missing change/proof or record an exact blocker; unchanged status narration is not progress. Session IDs, handoffs, receipts, and test logs are local in `.context/audit-fleet/`.

See [taste.md](taste.md) for the review standard. Preserve the user's current guidance edits.
