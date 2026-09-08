# What we are doing

Updated: 2026-09-08 04:11 UTC.

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

London owns the next review and integration. Both implementation workers are now idle; their delivered candidate is on the Terra branch at `d7f6567` and is **not yet merged into London**. Sol delivered the C1/C2 lifecycle (`6c88a18`, integrated by Terra as `40f5d2c`) and reports 55 focused passing tests. Terra delivered exact per-run adjusted-price cache reuse (`6e81338`, `f223972`) and an offline brief-comparison command (`d7f6567`). These are worker-reported checks, separate from the broad London results above. Sol retirement is held by the workspace service because detached-tool cleanup is unconfirmed; its work is preserved and repeated archive attempts have stopped.

- **C1/C2:** the delivered candidate wires the capture cutoff and actual readiness through production entrypoints, with pending decisions, separate execution claims, next-session opens, corporate actions, and legacy replay. London must review that lifecycle and its boundary proof before merging.
- **P4:** finish stable world membership and versioned snapshot/cluster semantics, retained-data cleanup/restore proof, and representative performance measurements. Generation pinning alone does not complete this package.
- **P5:** the replacement cache candidate keeps existing adjusted output and binds reuse to the current run and observation. Review its parity and concurrency proof before merging. The comparison command is offline measurement tooling; prompt changes and compaction remain disabled pending fresh quality/cost evidence.
- **P6:** first resolve the deployment-wrapper verification gate; actual old-owner termination, ledger/evidence reconciliation, rollback, and representative loaded operation remain prerequisites for consolidation. Fixture status labels cannot substitute for those observations.

## Cost and wakeups

Reuse the existing lead and bounded workers; keep one owner per shared file. Send short corrective handoffs with exact commits, consumer failures, and proof. The cache slice's repeated correction cost led to withholding it instead of launching another broad audit. Count coordination, correction, review, and model wake turns; reliable per-category token and dollar totals are currently unavailable, so no savings are claimed.

Native worker terminal callbacks remain available. The task-specific 15-minute fallback is loaded, but its model prompts are now suppressed while deliverables await London integration. The progress check found it repeatedly waking an idle lead without new source changes; that was unnecessary coordination cost. A new `review_pending_no_wake` guard is verified, and all five guard fixtures pass. Resume automatic model prompting only with concrete newly assigned work. Automatic push back into this Conductor chat has not been established; review remains London-owned.

Exact session IDs, source branches, receipts, and test logs are in the untracked `.context/audit-fleet/`. [taste.md](taste.md) records the recurring lessons at their source.
