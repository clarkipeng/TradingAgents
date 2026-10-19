# Stream A repair: atomic portfolio day lifecycle and sweep contract

Base: `fleet/round2` at `473136191e8971e723ab0dbb6897e8fd2fcad1fc`.

Read `/Users/clarkpeng/conductor/workspaces/TradingAgents/london/.context/handoffs/fleet/system-map.md` and `taste-canon.md` before editing.

Implement findings 1, 2, 5, and 6 only.

The required repair invariant is verbatim: one claim-owned transaction publishing state evidence, scenario seal, research-run record, and completed status together, no legacy visibility bypass.

A durable day record must have unique day identity and states `claimed`, `completed`, and `failed`.

Claim atomically so two invocations never both run.

A crash leaves a failed explicitly resumable claim and never exposes prior state as the current day.

`portfolio_state_asof` and `portfolio_report` read completed days only.

Do not introduce a standalone legacy helper that makes state visible or day completed outside the claim-owned transaction.

Research must be analysts-only, without debate or risk phases, and provide a validated compact brief per ticker.

The CIO remains the sole cross-sectional judge.

Use bounded parallelism of four workers, each with its own graph instance and tape identity, and never share `TradingAgentsGraph`.

Add a checked-in run policy with wall-clock deadline, the existing per-day research-call ceiling, default minimum ticker coverage of `0.8`, and complete quote coverage for held symbols.

Every breach must fail unsealed with a sanitized fixed-vocabulary outcome.

Day summaries must include coverage, research-call count spend proxy, and elapsed seconds.

Add focused tests for concurrent claim exclusion, failed/resumable claims, no visibility before successful transaction completion, completed-only projections, worker-local graph/tape identity, and policy breach outcomes.

Do not write `~/.tradingagents`, run pollers, launchctl, deploys, `temporal-portfolio-run`, provider calls, or LLM calls.

Run focused tests and `uv run python -m pytest -q`.

Commit with `type: what and why`, no co-author lines.

Report commit SHA, changed files, test results, exact full-suite outcome, and integration risks via `sightmesh parent --message`.
