"""Executable invariants for the portfolio day owner and sweep policy."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

from tradingagents import portfolio_run
from tradingagents.temporal import TemporalStore


def test_concurrent_claims_allow_one_owner(tmp_path):
    store = TemporalStore(tmp_path)
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda n: store.claim_portfolio_day("2026-09-01", n, claimed_at=now), ("a", "b")))
    assert len({claim["claim_id"] for claim in claims}) == 1
    assert all(claim["status"] == "claimed" for claim in claims)


def test_failed_claim_is_resumable_and_not_projected(tmp_path):
    store = TemporalStore(tmp_path)
    now = datetime.now(timezone.utc)
    store.claim_portfolio_day("2026-09-01", "a", claimed_at=now)
    store.fail_portfolio_day("2026-09-01", "a", "minimum_ticker_coverage")
    assert store.portfolio_states() == []
    assert store.claim_portfolio_day("2026-09-01", "b", claimed_at=now)["claim_id"] == "b"


def test_atomic_completion_projects_only_completed_day(tmp_path):
    store = TemporalStore(tmp_path)
    now = datetime.now(timezone.utc)
    store.claim_portfolio_day("2026-09-01", "a", claimed_at=now)
    store.complete_portfolio_day("2026-09-01", "a", as_of=now,
        state={"cash": Decimal("10"), "positions": {}, "equity": Decimal("10")},
        scenario_metadata={"kind": "portfolio-day"}, capture_run_id="run-a",
        decision="{}", report="{}")
    assert store.portfolio_state_asof(now)["portfolio_day"] == "2026-09-01"
    assert store.get_scenario("portfolio-2026-09-01") is not None
    assert store.get_research_run("run-a") is not None


def test_policy_breach_is_fixed_vocabulary_and_unsealed(tmp_path, monkeypatch):
    store = TemporalStore(tmp_path)
    monkeypatch.setattr(portfolio_run, "MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY", 1)
    result = portfolio_run.run_portfolio_day(store, ["A", "B"], day="2026-09-01",
        research_fn=lambda *_: {"rating": "Buy"}, complete_llm=lambda *_: "{}",
        quote_fn=lambda *_: Decimal("1"))
    assert result["status"] == "failed_unsealed"
    assert result["reason"] == "research_call_ceiling"
    assert store.get_scenario("portfolio-2026-09-01") is None


def test_deadline_breach_has_distinct_fixed_outcome(tmp_path, monkeypatch):
    store = TemporalStore(tmp_path)
    monkeypatch.setattr(portfolio_run, "PORTFOLIO_DAY_DEADLINE_SECONDS", 0)
    result = portfolio_run.run_portfolio_day(
        store, ["A"], day="2026-09-01",
        research_fn=lambda *_: {"rating": "Buy"},
        complete_llm=lambda *_: "{}",
        quote_fn=lambda *_: Decimal("1"),
    )
    assert result["status"] == "failed_unsealed"
    assert result["reason"] == "deadline_exceeded"
    assert store.get_scenario("portfolio-2026-09-01") is None
