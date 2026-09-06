# What we are doing

Updated: 2026-09-06.

Implementing the [audit fix plan](docs/audit-fix-plan-2026-09-06.md) through visible Sightmesh workers, as requested.
All 33 findings and four added validity concerns remain in scope.

## Execution

The visible Terra lead owns implementation and integration, with two bounded Luna workers for operations and database work.
A Sol reviewer checks the high-risk invariants after implementation; it will not duplicate routine review.
The lead carries the program through the documented gates and records any external blocker before promotion.
Exact session IDs, commits, checks, wake subscriptions, and residuals live in `.context/audit-fleet/`.

## Cost and wakeups

- At most two implementation workers under the lead; reuse owners and their context.
- Short messages only for blockers, required decisions, ready commits, failed proof, and completion.
- Focused tests per change; one combined integration suite at a coherent merge point.
- Durable handoffs carry evidence; avoid relaying logs and whole transcripts.
- Verify event delivery before calling a wakeup active. Use one sparse fallback check for missed events and remove it at completion.
- No diagnostic provider fetches, untracked paid retries, or incidental X-tier changes.

## Status

Plan, taste guidance, and coverage ledger are written and checked.
Three visible sessions are running from committed base `4d1fafd`: `ta-audit-lead` (Terra), `ta-luna-ops` (Luna), and `ta-luna-store` (Luna).
Both children have native terminal-event wakeups to their lead. The native delivery path was tested.
A task-scoped 15-minute fallback wakes the lead only when all known workers are idle; it retires on completion or a real external blocker.
This Conductor chat has no authenticated push callback, so autonomous continuation lives in the Sightmesh lead.
Focused baseline: 25 portfolio/lifecycle/replay tests passed in 2.84 seconds.
No implementation fix is marked complete yet; no production change has been made in this pass.
The previous sealed days still need replay and temporal-validity assessments.

## Finish gates

Visible failures and tested restore; one owner per day/chain; complete replay without external data; explicit quote/execution/spend policy; measured performance parity; safely retained old evidence.
Collector consolidation and rightsizing follow their operational gates, rather than an assumed calendar deadline.
See [taste.md](taste.md) for the review standard.
