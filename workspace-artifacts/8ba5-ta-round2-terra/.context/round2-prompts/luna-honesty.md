# Stream C: tape latch and evidence clocks

Base: `fleet/round2` at `f039695`.

Read the system map and taste canon at `/Users/clarkpeng/conductor/workspaces/TradingAgents/london/.context/handoffs/fleet/` before editing.

Implement findings 8 and 10.

- Make any `LangChainTapeRecorder` persistence failure latch the active temporal run invalid.
- A latched run cannot seal its scenario and must surface exactly one sanitized error class.
- TDD the latch through the portfolio-day path.
- In the GDELT importer, retain the actual fetch receipt as `observed_at`.
- Store `seendate` only as a provider-availability estimate in metadata.
- Make `available_at` policy explicit per the finding, preserving point-in-time semantics.

Do not write `~/.tradingagents`, run pollers, launchctl, deploys, `temporal-portfolio-run`, provider calls, or LLM calls.

Run affected tests and `python -m pytest -q`.

Commit with `type: what and why`, no co-author lines, and report commit SHA, changed files, test commands/results, and risks to the coordinator via `sightmesh parent --message`.
