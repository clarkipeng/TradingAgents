from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingagents.portfolio_run import _last_close, production_day_inputs
from tradingagents.temporal import (
    TemporalContext,
    TemporalMode,
    TemporalRunInvalidError,
    TemporalStore,
    temporal_context,
)


def test_production_child_graph_failure_latches_parent_context(tmp_path, monkeypatch):
    class Graph:
        def __init__(self, **_kwargs):
            pass

        def propagate(self, *_args, temporal, **_kwargs):
            raise RuntimeError("fixture child failure")

    class Client:
        def get_llm(self):
            return object()

    monkeypatch.setattr("tradingagents.graph.trading_graph.TradingAgentsGraph", Graph)
    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", lambda *_a, **_k: Client())
    store = TemporalStore(tmp_path)
    parent = TemporalContext.at(
        TemporalMode.LIVE_CAPTURE, datetime.now(timezone.utc), store=store, run_id="day"
    )
    research = production_day_inputs(store)["research_fn"]

    with pytest.raises(RuntimeError, match="fixture child failure"):
        research("NVDA", parent)
    with pytest.raises(TemporalRunInvalidError):
        parent.ensure_valid()


def test_production_quote_replays_through_temporal_boundary_without_provider(tmp_path, monkeypatch):
    class Client:
        def get_llm(self):
            return object()

    calls = []

    def vendor(_tool, symbol, _start, _end):
        calls.append(symbol)
        return "Date,Close\n2026-09-07,101.25\n"

    monkeypatch.setattr("tradingagents.llm_clients.factory.create_llm_client", lambda *_a, **_k: Client())
    monkeypatch.setattr("tradingagents.dataflows.interface.route_to_vendor", vendor)
    store = TemporalStore(tmp_path)
    quote = production_day_inputs(store)["quote_fn"]
    captured = TemporalContext.at(
        TemporalMode.LIVE_CAPTURE, datetime.now(timezone.utc), store=store, run_id="capture"
    )
    with temporal_context(captured):
        assert quote("NVDA", "2026-09-07").as_tuple().digits == (1, 0, 1, 2, 5)

    replay = TemporalContext.at(
        TemporalMode.REPLAY, captured.clock.as_of, store=store,
        run_id="replay", source_run_id="capture",
    )
    with temporal_context(replay):
        replayed = quote("NVDA", "2026-09-07")
    assert str(replayed) == "101.25"
    assert calls == ["NVDA"]


def test_last_close_uses_latest_eligible_session_not_csv_order():
    payload = "Date,Close\n2026-09-08,999\n2026-09-05,100\n2026-09-07,101\n"
    assert _last_close(payload, through="2026-09-07") == Decimal("101")
    assert _last_close("Date,Close\n2026-09-07,NaN\n", through="2026-09-07") is None
