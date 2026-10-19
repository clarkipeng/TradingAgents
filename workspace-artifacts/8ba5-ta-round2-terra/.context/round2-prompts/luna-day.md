# Stream A: atomic portfolio day lifecycle and sweep

Base: `fleet/round2` at `f039695`.

Read the system map and taste canon at `/Users/clarkpeng/conductor/workspaces/TradingAgents/london/.context/handoffs/fleet/` before editing.

Implement findings 1, 2, 5, and 6 in two sequential stages.

Stage 1 gives one portfolio day owner an atomic claim-to-complete lifecycle.

- Add a durable day record with unique day identity and states `claimed`, `completed`, and `failed`; claim atomically so concurrent invocations cannot both run.
- Commit the portfolio state, scenario seal, and research-run record together on completion.
- A crash leaves an explicitly resumable failed claim and never exposes prior state as the present day.
- Make `portfolio_state_asof` and `portfolio_report` read completed days only.

Stage 2 creates the sweep contract.

- Research is analysts-only, with no debate or risk phases, and produces a validated compact brief per ticker; CIO is the only cross-sectional judge.
- Use bounded parallelism of four workers with a graph instance and tape identity per worker; never share `TradingAgentsGraph`.
- Add a checked-in run policy with wall-clock deadline, existing per-day research-call ceiling, default minimum ticker coverage `0.8`, and complete quote coverage for held symbols.
- On policy breach, fail-unsealed with a sanitized fixed-vocabulary outcome.
- Add coverage, call-count spend proxy, and elapsed seconds to day summaries.

Write focused tests proving concurrent claim exclusion, failed/resumable days, completed-only projections, no shared graph/tape identity, and each policy breach.

Do not write `~/.tradingagents`, run pollers, launchctl, deploys, `temporal-portfolio-run`, provider calls, or LLM calls.

Run affected tests and `python -m pytest -q`.

Commit your finished implementation with `type: what and why`, no co-author lines.

Report the commit SHA, changed files, test commands/results, and any integration risk to the coordinator via `sightmesh parent --message`.
