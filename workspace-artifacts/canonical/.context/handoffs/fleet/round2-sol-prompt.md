# Assignment: round-two re-audit (sol, read-only)

Objective: verify that fleet round two actually fixed audit findings 1, 2, 4, 5, 6, 8, 10 from `audit-report.md` in this directory, at the current tip of branch `agent-trading-simulation-backtest` in YOUR worktree.

For each finding: quote its original failure scenario, then either demonstrate from the new code why that scenario is now impossible (cite file:line), or show it is still possible and rank it. Pay hardest attention to the atomic day lifecycle - two prior implementations were rejected for writing state before the claim-owned completion transaction; adversarially hunt for any remaining path where partial day output becomes visible to `portfolio_state_asof`, `portfolio_report`, or a rerun.

Also check for regressions the fixes may have introduced: the analysts-only sweep and worker parallelism (graph-instance sharing, tape identity per worker), the generation swap under concurrent readers, and the run-invalidation latch's interaction with the day lifecycle.

Read-only discipline: no repository writes, no live-store access, no provider spends; targeted pytest selections are fine.

Deliver `.context/handoffs/fleet/round2-audit.md` in the canonical checkout directory (same place as this file), sentence-per-line. Message your parent when complete.
