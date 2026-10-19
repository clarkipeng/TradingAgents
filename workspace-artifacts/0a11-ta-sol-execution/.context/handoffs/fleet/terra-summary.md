# Fleet cleanup integration summary

Branch: `fleet/cleanup-2026-08-31`.

Base: `agent-trading-simulation-backtest` at `1a70f38`.

Integrated commits: `0d5d724`, `cd74932`, `b40566f`, and `647e1a5`.

## Stream outcomes

- `luna-taste` moved portfolio-state and report evidence projections behind `TemporalStore` methods, centralized evidence-row decoding, and removed one unused clustering variable.
- `luna-harden` added the `MAX_LLM_CALLS_PER_PORTFOLIO_DAY` preflight and runtime budget guard plus an all-quotes-missing unsealed skip, with focused TDD coverage.
- `luna-ops` added `--media-db-url` with read-only SQLite and PostgreSQL paths, backend-neutral budget metadata parsing, and secret-safe store labels.
- `luna-docs` added `docs/system-overview.md` with current behavior, authority and freshness boundaries, sentence-per-line prose, and code links for durable invariants.

## Deferred items

- Wayback bridge parameters are unchanged.
  A bounded, read-only CDX reproduction timed out with zero bytes, so it could not establish a URL-normalization, time-window, or snapshot-availability defect in our bridge.
- The full suite is not green in this environment.
  Every merge-point full-suite attempt reached 16% and then stalled in unrelated deploy-collector subprocess tests.
  The affected tests pass below.

## Verification commands

- `uv run --with pytest python -m pytest -q` after integrating `0d5d724`.
- `uv run --with pytest python -m pytest -q` after integrating `cd74932`.
- `uv run --with pytest python -m pytest -q` after integrating `b40566f`.
- `uv run --with pytest python -m pytest -q` after integrating `647e1a5`.
- `uv run --with pytest python -m pytest -q tests/test_portfolio_run.py tests/test_verify_x_cycle.py` passed: 12 passed.
- `git diff --check 1a70f38..HEAD` passed.

## Review record

Each integrated diff was reviewed against the fleet taste canon before cherry-pick.

The resulting boundaries keep state decoding in its persistence owner, make day execution fail closed before sealing an empty portfolio, preserve a read-only verification surface, and document current behavior without copying the handoff inventory.
