# What we are doing

Updated: 2026-09-08 UTC.

Implementing the [audit fix plan](docs/audit-fix-plan-2026-09-06.md). All 33 findings and C1–C4 remain in scope. The program is **in progress**; local implementation does not establish deployment or operational verification.

## Integrated and locally verified

London includes the audit integration through `a51bcb5`, preserving the user's edits to `AGENTS.md` and `taste.md`.

- **Production replay:** the actual two-ticker, four-analyst graph, CIO, quote parsing, orders, fills, cash, and final state replay with models, data providers, context resolution, and socket access disabled. It rejects changed analyst output, changed quote derivation, and a deleted source model call. Source stream facts are checked against the seal. Exact prompt reordering is limited to parallel worker tapes; sequential tapes retain their order. The four production cases and adapter tests passed 16 checks.
- **Spend and failures:** real synchronous/asynchronous LangChain admission rejects spending before the provider is called; durable daily reservations include output allowances, CIO headroom, retries, actual returned usage, and ambiguous attempts. Provider/CIO failure cannot seal a successful fallback day. Policy-valued cost is distinct from provider billing.
- **Storage and retrieval:** generation publication rolls back as a unit, failed builds can retry, retained run/scenario references protect their inputs, and search tools carry the run's pinned generation. The storage/ranking/benchmark gate passed 50 tests. This establishes local correctness, not representative production performance or completion of every P4 requirement.
- **Backup and mirror:** one verified unit contains the temporal database, referenced artifacts, and spend ledger. Publication preserves the previous valid unit on corruption. Restore and the actual sync script install a complete root mirror, preserve ambiguous spend, and refuse an active portfolio writer. The operations gate passed 63 tests; the final backup-specific recheck passed 8.
- **Required checks:** repository-wide Ruff passes. The broad suite excluding deployment-wrapper and external-service tests completed with 1,743 passed, one optional dependency skip, and two stale fixture failures; those fixtures were corrected and all 10 focused checks passed. The deployment-wrapper gate remains open: its unchanged success fixture exceeded the original 10-second subprocess limit in broad and isolated runs. A separate diagnostic finished successfully in 6.34 seconds; that does not establish a reliable gate. Repeated runs were stopped after confirming the failure. Service-backed checks were not run.

No production deployment, paid quality experiment, or infrastructure cutover has been performed. Broad verification exposed an unnecessary Yahoo identity lookup in a memory fixture and a fake-clock pacing stall; both test boundaries were isolated before the final broad run. Existing sealed days still need their individual replay and temporal-validity assessments.

## Active ownership and remaining gates

London owns integration and this status. A visible Sol worker owns C1/C2 production execution, including the overlapping portfolio/store lifecycle paths. The existing Sightmesh Terra lead owns P5 cache parity and continuation; it defers overlapping P4 store work until Sol delivers. The Luna P4/operations work is integrated, and its rejected P5 source remains preserved for reconciliation. Additional launch attempts stopped when the host reported its coding-agent capacity limit.

- **C1/C2:** historical data boundaries and replay are implemented, but the actual capture cutoff must reach the production decision context. Sol is extending the candidate next-session execution helper: separate durable pending decisions/executions, real driver wiring, corporate actions, calendar/timing, and opening-gap proof remain before integration. Legacy close-based replay retains its original meaning.
- **P4:** finish stable world membership and versioned snapshot/cluster semantics, retained-data cleanup/restore proof, and representative performance measurements. Generation pinning alone does not complete this package.
- **P5:** cache candidates were held back for changing price adjustment semantics, unsafe publication, and incomplete vintage/coverage contracts. Preserve current outputs while proving per-run reuse. Prompt/brief changes remain disabled until a separate bounded quality/cost comparison supplies evidence; a baseline compared with itself is not that evidence.
- **P6:** first resolve the deployment-wrapper verification gate; actual old-owner termination, ledger/evidence reconciliation, rollback, and representative loaded operation remain prerequisites for consolidation. Fixture status labels cannot substitute for those observations.

## Cost and wakeups

Reuse the existing lead and bounded workers; keep one owner per shared file. Send short corrective handoffs with exact commits, consumer failures, and proof. The cache slice's repeated correction cost led to withholding it instead of launching another broad audit. Count coordination, correction, review, and model wake turns; reliable per-category token and dollar totals are currently unavailable, so no savings are claimed.

Native worker terminal callbacks are primary. The task-specific 15-minute fallback checks only known sessions, skips active coding work and pending approvals, and deduplicates idle prompts. Its four fixtures pass. On 2026-09-08 the plist was present but unloaded; London restored the job, verified it loaded, and observed a successful scheduled wake receipt. It retires only at completion or a real external gate. Automatic push back into this Conductor chat has not been established; the visible Terra lead is the continuation owner.

Exact session IDs, source branches, receipts, and test logs are in the untracked `.context/audit-fleet/`. [taste.md](taste.md) records the recurring lessons at their source.
