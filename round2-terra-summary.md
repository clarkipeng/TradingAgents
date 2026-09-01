# Fleet round two integration summary

Branch: `fleet/round2` based on `f039695`.

## Stream B: generational reindex

Integrated as `f496ebb feat: publish temporal document generations atomically`.

The temporal document index rebuild now uses a pinned evidence high-water mark, bounded 500-row shadow-table batches, one delta catch-up transaction, and an atomic table-generation swap.

Readers use published canonical tables only.

Search manifests and traces carry the active generation identifier.

The `temporal-reindex` CLI states that the 21:30 launchd job remains unloaded until the owner re-arms it.

Focused worker verification passed: `uv run --with pytest pytest -q tests/test_temporal_documents.py` with 15 tests and temporal search/core/langchain/integration tests with 25 tests.

## Stream C: tape latch and GDELT clocks

Integrated as `0c41a21 fix: latch temporal tape failures and preserve GDELT receipt clocks`.

Temporal tape persistence failures irreversibly invalidate the active run and sealing fails with one sanitized `TemporalRunInvalidError`.

GDELT stores the fetch receipt as `observed_at` and `available_at`, and preserves `seendate` only as the provider availability estimate metadata.

Focused worker verification passed: `uv run --with pytest pytest -q tests/test_temporal_gdelt.py tests/test_temporal_gdelt_wayback.py tests/test_temporal_langchain_adapter.py tests/test_portfolio_run.py` with 28 tests.

## Combined integration verification

`uv run python -m pytest -q tests/test_temporal_documents.py tests/test_temporal_search.py tests/test_temporal_core.py tests/test_temporal_langchain_adapter.py tests/test_temporal_gdelt.py tests/test_temporal_gdelt_wayback.py tests/test_portfolio_run.py` passed with 58 tests.

`uv run python -m pytest -q` began cleanly through 12% after Stream C integration, but the terminal execution limit stopped it before completion.

The literal `python -m pytest -q` command is unavailable because this workspace has no `python` executable.

`uv run python -m pytest -q` is the locked-environment equivalent.

## Deferred Stream A: atomic portfolio day and bounded sweep

Findings 1, 2, 5, and 6 are deferred because every delivered candidate violates the required single atomic completion boundary.

Candidate `7c47776` records state and registers a completed legacy day before the claim-owned scenario and research-run transaction.

Candidate `d1f6559` likewise marks the day complete through a direct state-recording path and also leaves an undefined CIO recorder reference.

The visible Stream A worker is offline, so the corrective handoff could not be delivered.

Do not merge either candidate.

The required repair is one claim-owned transaction that publishes state evidence, scenario seal, research-run record, and completed day status together, with no legacy production visibility bypass.

## Exact verification commands

`uv run python -m pytest -q tests/test_temporal_documents.py tests/test_temporal_search.py tests/test_temporal_core.py tests/test_temporal_langchain_adapter.py tests/test_temporal_gdelt.py tests/test_temporal_gdelt_wayback.py tests/test_portfolio_run.py`

`uv run python -m pytest -q`

`git diff --check f039695..fleet/round2`
