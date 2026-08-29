"""The daily portfolio loop: sealed state, CIO allocation, rebalance orders."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingagents import portfolio_run
from tradingagents.temporal import TemporalStore

UTC = timezone.utc
DAY_END = datetime(2026, 8, 28, 21, 30, tzinfo=UTC)


@pytest.mark.unit
def test_portfolio_state_seals_and_reads_point_in_time(tmp_path):
    store = TemporalStore(tmp_path)

    initial = portfolio_run.portfolio_state_asof(store, DAY_END)
    assert initial == {
        "portfolio_day": None,
        "cash": "100000",
        "positions": {},
        "equity": None,
    }

    portfolio_run.record_portfolio_state(
        store,
        {"cash": "60000", "positions": {"NVDA": "200"}, "equity": "104000"},
        day="2026-08-28",
        available_at=DAY_END,
    )

    later = portfolio_run.portfolio_state_asof(store, DAY_END)
    assert later["portfolio_day"] == "2026-08-28"
    assert later["positions"] == {"NVDA": "200"}
    # The day before, that state must be invisible.
    earlier = portfolio_run.portfolio_state_asof(
        store, datetime(2026, 8, 27, 21, 30, tzinfo=UTC)
    )
    assert earlier["portfolio_day"] is None


@pytest.mark.unit
def test_cio_falls_back_to_deterministic_weights_on_invalid_output():
    ratings = {"NVDA": "Overweight", "TSLA": "Sell", "MSFT": "Hold"}

    def bad_llm(prompt: str) -> str:
        return '{"weights": {"DOGE": 5.0}}'  # unknown ticker, absurd weight

    plan = portfolio_run.cio_allocate(
        ratings, briefs={}, constraints=portfolio_run.DEFAULT_CONSTRAINTS,
        complete_llm=bad_llm,
    )
    assert plan["source"] == "deterministic-fallback"
    assert "unknown" in plan["fallback_reason"] or "weight" in plan["fallback_reason"]
    assert plan["weights"]["NVDA"] > 0
    assert plan["weights"].get("TSLA", 0.0) == 0.0  # Sell scores negative, long-only
    assert sum(plan["weights"].values()) <= 1.0 + 1e-9


@pytest.mark.unit
def test_cio_accepts_valid_llm_weights_within_constraints():
    ratings = {"NVDA": "Overweight", "MSFT": "Buy"}

    def good_llm(prompt: str) -> str:
        assert "NVDA" in prompt and "max_weight" in prompt
        return '{"weights": {"NVDA": 0.10, "MSFT": 0.05}, "rationale": {"NVDA": "supply [evidence:ev-1]"}}'

    plan = portfolio_run.cio_allocate(
        ratings, briefs={"NVDA": "chip supply brief"},
        constraints=portfolio_run.DEFAULT_CONSTRAINTS, complete_llm=good_llm,
    )
    assert plan["source"] == "cio-llm"
    assert plan["weights"] == {"NVDA": 0.10, "MSFT": 0.05}
    assert plan["rationale"]["NVDA"].endswith("[evidence:ev-1]")


@pytest.mark.unit
def test_run_portfolio_day_seals_state_and_next_day_reads_it(tmp_path):
    store = TemporalStore(tmp_path)
    quotes = {"NVDA": Decimal("500"), "MSFT": Decimal("400")}

    def research(ticker, context):
        assert context.mode.value == "live_capture"
        if ticker == "MSFT":
            raise RuntimeError("sweep hiccup")  # one failure must not kill the day
        return {"rating": "Overweight", "brief": f"{ticker} looks strong"}

    def llm(prompt):
        return '{"weights": {"NVDA": 0.10}, "rationale": {"NVDA": "solid [evidence:e1]"}}'

    result = portfolio_run.run_portfolio_day(
        store,
        ["NVDA", "MSFT"],
        day="2026-08-28",
        research_fn=research,
        complete_llm=llm,
        quote_fn=lambda symbol, day: quotes.get(symbol),
    )

    assert result["failures"] == ["MSFT: RuntimeError"]
    assert result["plan"]["source"] == "cio-llm"
    # 100k equity * 10% = 10k -> 20 shares at 500.
    assert result["fills"][0]["symbol"] == "NVDA"
    assert result["fills"][0]["quantity"] == "20"
    assert Decimal(result["equity"]) == pytest.approx(Decimal("100000"), abs=Decimal("50"))

    scenario = store.get_scenario("portfolio-2026-08-28")
    assert scenario is not None
    assert scenario.basis == "forward-captured"

    # The next day starts from this day's sealed positions.
    next_day = portfolio_run.portfolio_state_asof(
        store, datetime(2026, 8, 29, 21, 30, tzinfo=UTC)
    )
    assert next_day["portfolio_day"] == "2026-08-28"
    assert next_day["positions"] == {"NVDA": "20"}


@pytest.mark.unit
def test_last_close_parses_csv_and_degrades_on_garbage():
    csv_payload = "Date,Open,High,Low,Close,Volume\n2026-08-27,1,2,0,499.5,10\n2026-08-28,2,3,1,505.25,12\n"
    assert portfolio_run._last_close(csv_payload) == Decimal("505.25")
    assert portfolio_run._last_close("NO_DATA_AVAILABLE: nothing") is None
    assert portfolio_run._last_close("not,a\nreal,csv") is None
    assert portfolio_run._last_close(None) is None


@pytest.mark.unit
def test_rebalance_orders_sell_before_buy_and_respect_cash():
    state = {"cash": "10000", "positions": {"TSLA": "100"}}
    quotes = {"TSLA": Decimal("200"), "NVDA": Decimal("500")}
    # Equity = 10000 + 100*200 = 30000. Target: all NVDA at 50%, drop TSLA.
    orders = portfolio_run.rebalance_orders(
        state, {"NVDA": 0.5, "TSLA": 0.0}, quotes, submitted_at=DAY_END
    )
    sides = [(order.symbol, order.side.value, order.quantity) for order in orders]
    assert sides[0] == ("TSLA", "SELL", Decimal("100"))  # sells free cash first
    assert sides[1][0] == "NVDA"
    assert sides[1][2] == Decimal("30")  # floor(30000*0.5 / 500)

    # No-op deltas produce no orders.
    assert portfolio_run.rebalance_orders(
        {"cash": "1000", "positions": {}}, {}, {}, submitted_at=DAY_END
    ) == []
