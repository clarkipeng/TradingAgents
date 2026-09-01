"""Executable invariants for the portfolio day owner and sweep policy."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

from tradingagents import portfolio_run
from tradingagents.temporal import TemporalStore


def test_concurrent_claims_have_one_owner(tmp_path):
    store = TemporalStore(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: store.claim_portfolio_day(
            "2026-09-01", "portfolio-2026-09-01", f"run-{n}"), range(2)))
    assert sorted(results) == ["busy", "claimed"]
    assert {store.portfolio_day("2026-09-01")["run_id"]} <= {"run-0", "run-1"}


def test_failed_day_is_hidden_and_resumable(tmp_path):
    store = TemporalStore(tmp_path)
    store.claim_portfolio_day("2026-09-01", "portfolio-2026-09-01", "run-1")
    portfolio_run.record_portfolio_state(
        store, {"cash": "1", "positions": {}, "equity": "1"},
        day="2026-09-01", available_at=datetime(2026, 9, 1, 21, 30, tzinfo=timezone.utc),
    )
    store.fail_portfolio_day("2026-09-01", "execution_failed")
    assert store.portfolio_states() == []
    assert store.claim_portfolio_day("2026-09-01", "portfolio-2026-09-01", "run-2") == "claimed"


def test_completed_projections_require_completed_day(tmp_path):
    store = TemporalStore(tmp_path)
    portfolio_run.record_portfolio_state(
        store, {"cash": "1", "positions": {"A": "1"}, "equity": "2"},
        day="2026-09-01", available_at=datetime(2026, 9, 1, 21, 30, tzinfo=timezone.utc),
    )
    assert store.portfolio_state_asof(datetime(2026, 9, 2, tzinfo=timezone.utc))["portfolio_day"] == "2026-09-01"


def test_policy_has_bounded_parallelism_and_default_coverage():
    policy = portfolio_run.load_portfolio_run_policy()
    assert policy["max_workers"] == 4
    assert policy["min_ticker_coverage"] == 0.8
    assert policy["require_held_quotes"] is True
