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


def test_thin_quote_coverage_fails_the_day_instead_of_shrinking_the_universe(tmp_path):
    """Round-two re-audit, finding 5 residual: researched tickers without
    quotes silently vanished from the CIO universe; they must count against
    coverage so a mostly-unquotable day fails unsealed."""
    store = TemporalStore(tmp_path)
    result = portfolio_run.run_portfolio_day(
        store, ["A", "B", "C"], day="2026-09-01",
        research_fn=lambda ticker, context: {"rating": "Buy", "brief": "b"},
        complete_llm=lambda *_: pytest.fail("thin quotes must stop the CIO"),
        quote_fn=lambda symbol, day: Decimal("10") if symbol == "A" else None,
    )
    assert result["status"] == "failed_unsealed"
    assert result["reason"] == "quote_coverage"
    assert store.get_scenario("portfolio-2026-09-01") is None


def test_deadline_interrupts_a_hung_research_call(tmp_path, monkeypatch):
    """Round-two re-audit, finding 5 residual: the deadline was checked only
    before calls, so one hung provider call held the day open forever."""
    import threading

    store = TemporalStore(tmp_path)
    monkeypatch.setattr(portfolio_run, "PORTFOLIO_DAY_DEADLINE_SECONDS", 1)
    release = threading.Event()

    def hung_research(ticker, context):
        release.wait(30)
        return {"rating": "Buy", "brief": "late"}

    import time as time_module
    started = time_module.monotonic()
    result = portfolio_run.run_portfolio_day(
        store, ["A"], day="2026-09-01",
        research_fn=hung_research,
        complete_llm=lambda *_: "{}",
        quote_fn=lambda *_: Decimal("1"),
    )
    elapsed = time_module.monotonic() - started
    release.set()
    assert result["status"] == "failed_unsealed"
    assert result["reason"] == "deadline_exceeded"
    assert elapsed < 10  # the hung call could not hold the day open


def test_abandoned_claim_recovers_after_staleness(tmp_path):
    """Round-two re-audit, finding 1 residual: a process death left a day
    claimed forever. A claim older than the staleness window is abandoned
    and a new owner takes over."""
    from datetime import datetime, timedelta, timezone

    store = TemporalStore(tmp_path)
    stale_moment = datetime.now(timezone.utc) - timedelta(
        seconds=portfolio_run.PORTFOLIO_CLAIM_STALE_SECONDS + 60
    )
    store.claim_portfolio_day("2026-09-01", "dead-owner", claimed_at=stale_moment)

    fresh = store.claim_portfolio_day(
        "2026-09-01", "new-owner", claimed_at=datetime.now(timezone.utc)
    )
    assert fresh == {"day": "2026-09-01", "claim_id": "new-owner", "status": "claimed"}

    # A live claim inside the window is never stolen.
    held = store.claim_portfolio_day(
        "2026-09-01", "third-owner", claimed_at=datetime.now(timezone.utc)
    )
    assert held["claim_id"] == "new-owner"


def test_projections_trust_only_the_completion_owned_state(tmp_path):
    """Round-two re-audit, finding 2: sol planted an orphan state (equity 999)
    on a completed day and both projections believed it. The lifecycle row
    now names its exact state evidence; anything else is invisible."""
    from datetime import datetime, timezone

    store = TemporalStore(tmp_path)
    as_of = datetime(2026, 9, 1, 21, 30, tzinfo=timezone.utc)

    # The adversarial orphan, written outside any completion.
    portfolio_run.record_portfolio_state(
        store, {"cash": "999", "positions": {}, "equity": "999"},
        day="2026-09-01", available_at=as_of,
    )

    store.claim_portfolio_day("2026-09-01", "owner", claimed_at=as_of)
    store.complete_portfolio_day(
        "2026-09-01", "owner", as_of=as_of,
        state={"cash": "100", "positions": {"NVDA": "1"}, "equity": "100"},
        scenario_metadata={"kind": "portfolio-day"},
        capture_run_id="run-identity-test",
        decision="{}", report="{}",
    )

    states = store.portfolio_states()
    assert [state["equity"] for state in states] == ["100"]
    assert store.portfolio_state_asof(as_of)["equity"] == "100"
