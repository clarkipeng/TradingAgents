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

## Stream A: atomic portfolio day and bounded sweep

Integrated as `7e2a07e fix: atomically seal portfolio day lifecycle`, `59d62c5 fix: distinguish portfolio deadline breaches`, and `15b6d40 fix: preserve latched portfolio run failures`.

The final design gives a portfolio day one durable, atomic claim-to-completion owner.

State evidence, scenario seal, research-run record, and completed status publish together from an active claim.

Failed claims remain resumable and portfolio projections read completed days only.

The analysts-only sweep uses at most four worker-local graph and tape identities, applies the checked-in deadline, call ceiling, 0.8 coverage, and held-quote policy, and emits fixed failure outcomes plus coverage, call count, and elapsed-time summary fields.

Focused Stream A worker verification passed with 17 tests.

The final combined integration verification passed with 48 tests.

## Exact verification commands

`uv run python -m pytest -q tests/test_temporal_documents.py tests/test_temporal_search.py tests/test_temporal_core.py tests/test_temporal_langchain_adapter.py tests/test_temporal_gdelt.py tests/test_temporal_gdelt_wayback.py tests/test_portfolio_run.py`

`uv run python -m pytest -q`

`git diff --check f039695..fleet/round2`
