"""The daily portfolio loop: sealed state, CIO allocation, rebalance orders."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from tradingagents import portfolio_run
from tradingagents.temporal import TemporalRunInvalidError, TemporalStore

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
def test_temporal_store_owns_portfolio_state_projections(tmp_path):
    store = TemporalStore(tmp_path)
    portfolio_run.record_portfolio_state(
        store,
        {"cash": "60000", "positions": {"NVDA": "20"}, "equity": "70000"},
        day="2026-08-28",
        available_at=DAY_END,
    )

    assert store.portfolio_state_asof(DAY_END)["portfolio_day"] == "2026-08-28"
    assert store.portfolio_states() == [{
        "portfolio_day": "2026-08-28",
        "cash": "60000",
        "positions": {"NVDA": "20"},
        "equity": "70000",
    }]


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
def test_run_portfolio_day_does_not_seal_when_tape_persistence_latches_run(tmp_path, monkeypatch):
    store = TemporalStore(tmp_path)
    from tradingagents.temporal_adapters.langchain import LangChainTapeRecorder

    recorder = LangChainTapeRecorder(store)

    def fail(*args, **kwargs):
        raise RuntimeError("storage detail must not escape")

    monkeypatch.setattr(store, "begin_llm_call", fail)

    def research(ticker, context):
        recorder.on_llm_start({"name": "test-model"}, [ticker], run_id=uuid4())
        return {"rating": "Buy", "brief": "brief"}

    with pytest.raises(TemporalRunInvalidError, match="^temporal run is invalid$"):
        portfolio_run.run_portfolio_day(
            store,
            ["NVDA"],
            day="2026-08-28",
            research_fn=research,
            complete_llm=lambda prompt: '{"weights": {"NVDA": 0.10}}',
            quote_fn=lambda symbol, day: Decimal("500"),
        )

    assert store.get_scenario("portfolio-2026-08-28") is None


@pytest.mark.unit
def test_run_portfolio_day_skips_an_already_sealed_day_without_spending(tmp_path):
    store = TemporalStore(tmp_path)
    store.seal_scenario(
        "portfolio-2026-08-31",
        as_of=datetime(2026, 8, 31, 21, 30, tzinfo=UTC),
        basis="forward-captured",
    )

    result = portfolio_run.run_portfolio_day(
        store,
        ["NVDA"],
        day="2026-08-31",
        research_fn=lambda *a: pytest.fail("a sealed day must not research"),
        complete_llm=lambda *a: pytest.fail("a sealed day must not call the CIO"),
        quote_fn=lambda *a: pytest.fail("a sealed day must not fetch quotes"),
    )
    assert result["skipped"] == "day already sealed"
    assert result["scenario_id"] == "portfolio-2026-08-31"


@pytest.mark.unit
def test_run_portfolio_day_skips_before_llm_budget_is_exceeded(tmp_path, monkeypatch, caplog):
    store = TemporalStore(tmp_path)
    monkeypatch.setattr(portfolio_run, "MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY", 1)

    result = portfolio_run.run_portfolio_day(
        store,
        ["NVDA"],
        day="2026-08-31",
        research_fn=lambda *a: pytest.fail("budget must stop research"),
        complete_llm=lambda *a: pytest.fail("budget must stop CIO"),
        quote_fn=lambda *a: pytest.fail("budget must stop quote fetch"),
    )

    assert result["skipped"] == "research call budget exceeded"
    assert result["research_call_budget"] == 1
    assert "research call budget exceeded" in caplog.text
    assert store.get_scenario("portfolio-2026-08-31") is None


@pytest.mark.unit
def test_run_portfolio_day_skips_without_sealing_when_all_quotes_are_missing(tmp_path, caplog):
    store = TemporalStore(tmp_path)

    result = portfolio_run.run_portfolio_day(
        store,
        ["NVDA"],
        day="2026-08-31",
        research_fn=lambda ticker, context: {"rating": "Buy", "brief": "brief"},
        complete_llm=lambda *a: pytest.fail("missing quotes must skip CIO"),
        quote_fn=lambda *a: None,
    )

    assert result["skipped"] == "all quotes missing"
    assert result["failures"] == ["NVDA: no quote"]
    assert "all quotes missing" in caplog.text
    assert store.get_scenario("portfolio-2026-08-31") is None


@pytest.mark.unit
def test_portfolio_report_tracks_equity_by_day_and_benchmark(tmp_path):
    store = TemporalStore(tmp_path)
    for day, equity, cash in [
        ("2026-08-28", "100000", "100000"),
        ("2026-08-31", "101500", "70000"),
        ("2026-09-01", "99800", "70000"),
    ]:
        portfolio_run.record_portfolio_state(
            store,
            {"cash": cash, "positions": {"NVDA": "43"} if cash != "100000" else {}, "equity": equity},
            day=day,
            available_at=datetime.fromisoformat(f"{day}T21:30:00+00:00"),
        )

    report = portfolio_run.portfolio_report(
        store, benchmark_closes={"2026-08-28": 640.0, "2026-09-01": 646.4},
    )

    assert [row["day"] for row in report["days"]] == ["2026-08-28", "2026-08-31", "2026-09-01"]
    assert report["days"][1]["daily_return_pct"] == pytest.approx(1.5)
    assert report["total_return_pct"] == pytest.approx(-0.2)
    assert report["benchmark_return_pct"] == pytest.approx(1.0)
    assert report["latest"]["positions"] == {"NVDA": "43"}
    assert report["latest"]["equity"] == "99800"

    empty = portfolio_run.portfolio_report(TemporalStore(tmp_path / "empty"))
    assert empty["days"] == []


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
