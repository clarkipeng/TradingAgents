# Assignment: system audit and insights (sol, read-only)

Objective: audit the TradingAgents implementation at base 1a70f38 against the owner's taste canon and produce ranked findings plus genuinely new insights.

Read first: `.context/handoffs/fleet/system-map.md` and `.context/handoffs/fleet/taste-canon.md`.

Scope, in priority order:
1. The surfaces built in the last two weeks: `tradingagents/portfolio_run.py`, `tradingagents/dataflows/x_roster.py`, the `x_posts` tool in `tradingagents/temporal_adapters/langchain.py`, `poll_x_roster_once` in `tradingagents/poller.py`, `tradingagents/temporal_collectors/{gdelt,gdelt_wayback,media_store}.py`, `tradingagents/temporal/store.py` (clustering, deferred_clustering, media_posts_asof), `scripts/{sync_cloud_media.sh,run_portfolio_day.sh,verify_x_cycle.py,collector_health.sh,deploy_collector.sh,plan_a_experiment.py}`, and `cli/main.py` commands added for them.
2. Cross-cutting invariants: the single-mutator write lock, availability clocks, sealed-scenario immutability, budget receipts, sanitized failure boundaries.
3. The experiment integrity chain: tapes, traces, rubrics, scoring.

For each finding give: file:line, the violated invariant or taste rule, a concrete failure scenario, and the smallest robust fix. Rank by severity. Separate a final section titled `New insights` for observations that are not defects: design tensions, scaling cliffs ahead, measurement blind spots, opportunities the owner has not asked for.

Verification discipline: read the code, run read-only commands and targeted pytest selections freely, but never mutate `~/.tradingagents`, never spend provider budgets, never write to the repository.

Deliver: `.context/handoffs/fleet/audit-report.md` in your workspace, sentence-per-line Markdown. Message your parent when the report is complete. Stop after delivering; do not fix anything yourself.
