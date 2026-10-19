# Assignment: cleanup and hardening fleet manager (terra)

Objective: land the fleet TODO inventory as clean committed work based on branch `agent-trading-simulation-backtest` at 1a70f38. You coordinate; luna workers implement.

Read first: `.context/handoffs/fleet/system-map.md` (the TODO inventory and hard guardrails are law) and `.context/handoffs/fleet/taste-canon.md` (the review standard).

Spawn exactly four luna workers (`--profile codex-luna --worktree --unattended`), one per stream, and run them in parallel:

1. `luna-taste`: taste-alignment cleanup. Move the portfolio state and report queries off `store._connect()` onto owned `TemporalStore` methods (system-map item 4), sweep the modules named in the sol scope for dead variables, duplicated helpers, oversized functions, and comment rot, and keep behavior identical. Proof: full pytest green.
2. `luna-harden`: portfolio day hardening (item 3). Add a per-day LLM call budget cap enforced inside `run_portfolio_day` (config constant, loud skip when exceeded) and an all-quotes-missing guard that skips the day without sealing garbage. TDD both.
3. `luna-ops`: verification honesty (items 1 and 2). Teach `verify_x_cycle.py` a `--media-db-url` option so its X and roster checks can run against the cloud store, and diagnose the Wayback zero-bodies problem with a bounded read-only reproduction, fixing the bridge parameters if the defect is ours. No live spends beyond a handful of free CDX/Wayback reads.
4. `luna-docs`: write `docs/system-overview.md` from the system map (current behavior only, sentence-per-line, invariants linked to their owning code), and add the missing freshness or authority notes the taste canon demands for new docs. No code changes.

Merge discipline: review each luna's diff against the taste canon yourself, run `python -m pytest -q` at the merge point after each integration, and integrate streams one at a time into a single branch `fleet/cleanup-2026-08-31` in your worktree. Never push to the base branch; the owner gates the final merge. Commit messages follow repository style: `type: what changed and why`, no co-author lines.

Known failure mode of your role: stalling at stream boundaries and retiring workers early. Drive each boundary explicitly - when a luna reports done, verify its proof ran, integrate immediately, and only then retire it.

Deliver: the integrated branch, plus `.context/handoffs/fleet/terra-summary.md` listing per-stream outcomes, deferred items with reasons, and the exact verification commands you ran. Message your parent when integration is complete or when blocked more than 30 minutes.
