# Luna P5 handoff

Date: 2026-09-08

## Commit

`fc3040f5211c13288c0b624fa9810fbe209e680b` — P5 findings 17/18/27 bounded
dataflow and prompt-evaluation slice.

## Implemented

- `stockstats_utils.py` now owns the versioned OHLCV cache path, metadata
  sidecar, provider/adjustment policy, atomic cache writes, bounded profile
  cache keyed by normalized symbol plus vintage, and cache-window reads that
  honor same-day TTL and stale-data guards.
- `y_finance.get_YFin_data_online` reuses the shared captured OHLCV vintage and
  publishes provider results for stockstats siblings. `get_fundamentals`
  reuses same-vintage profile data. No live provider was called in tests.
- `tradingagents/analyst_brief.py` adds deterministic per-analyst sections,
  exact report text preservation, token accounting, citation/date extraction,
  versioned prompt metadata, and a frozen-text comparison path. Compaction is
  explicitly disabled and enabling it raises until quality/cost evidence exists.
  Existing prompt/tape paths were not changed.

## Proof

`PYTHONPATH=$PWD uv run --with pytest pytest -q tests/test_p5_cache_and_prompt.py tests/test_analyst_brief.py tests/test_date_boundaries.py tests/test_yfinance_stale_ohlcv_guard.py tests/test_ohlcv_cache_freshness.py tests/test_stockstats_date_column.py`
— **29 passed**. Python compilation and `git diff --check` passed.

## Residuals / integration request

Portfolio/graph/adapters were intentionally untouched. Lead must select the
portfolio integration seam and provide a frozen-scenario quality/cost
comparison artifact before enabling any model-visible brief compaction. The
comparison helper currently reports `evidence_pending=True`; no provider or
model calls were purchased.

Explicit reconciliation notes:

- `yf.download(auto_adjust=False)` and adjusted-history consumers share the
  current cache policy/path; parent Terra owns any invariant correction.
- A partial history response is cached under the full five-year window name;
  the metadata records the policy but does not prove full-window coverage.
- Legacy cache files with missing metadata are intentionally accepted for
  compatibility.
- Brief token accounting counts whitespace-delimited words, not model tokens;
  no model-token estimator or quality claim is present.
