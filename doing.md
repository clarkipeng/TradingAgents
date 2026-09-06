# What we are doing

Updated: 2026-09-06.

Implementing the [audit fix plan](docs/audit-fix-plan-2026-09-06.md) through visible Sightmesh workers, as requested.
All 33 findings and four added validity concerns remain in scope.

## Execution

London is the release captain and owns final integration and production gates.
A Terra lead will implement the coupled replay/portfolio work and integrate two bounded Luna workers for operations and database work.
A Sol reviewer will check the high-risk invariants after implementation; it will not duplicate routine review.
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
Worker launch and wake verification are being prepared.
No implementation fix is marked complete yet; no production change has been made in this pass.
The previous sealed days still need replay and temporal-validity assessments.

## Finish gates

Visible failures and tested restore; one owner per day/chain; complete replay without external data; explicit quote/execution/spend policy; measured performance parity; safely retained old evidence.
Collector consolidation and rightsizing follow their operational gates, rather than an assumed calendar deadline.
See [taste.md](taste.md) for the review standard.
