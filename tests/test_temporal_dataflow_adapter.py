from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.config import set_config
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.temporal import TemporalContext, TemporalMode, TemporalStore, temporal_context
from tradingagents.temporal.runtime import current_context
from tradingagents.temporal_adapters.tradingagents import invoke_tool

UTC = timezone.utc


def test_router_capture_and_replay_preserve_existing_return_shape(tmp_path, monkeypatch):
    set_config({"data_vendors": {"news_data": "yfinance"}})
    calls = 0

    def current_news(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return "news payload"

    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"], "yfinance", current_news)
    store = TemporalStore(tmp_path)
    capture = TemporalContext.at(TemporalMode.LIVE_CAPTURE, datetime.now(UTC), store=store)

    with temporal_context(capture):
        captured = interface.route_to_vendor("get_news", "NVDA", "2025-01-01", "2025-01-02")

    assert captured == "news payload"
    assert calls == 1
    evidence = store.latest_eligible(
        "dataflow.get_news",
        {"args": ["NVDA", "2025-01-01", "2025-01-02"], "kwargs": {}},
        as_of=datetime.now(UTC),
    )
    assert evidence is not None

    replay = TemporalContext.at(
        TemporalMode.REPLAY,
        evidence.available_at + timedelta(microseconds=1),
        store=store,
    )
    with temporal_context(replay):
        replayed = interface.route_to_vendor("get_news", "NVDA", "2025-01-01", "2025-01-02")

    assert replayed == "news payload"
    assert calls == 1


def test_router_replay_miss_never_calls_a_vendor(tmp_path, monkeypatch):
    set_config({"data_vendors": {"news_data": "yfinance"}})
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_news"],
        "yfinance",
        lambda *_args, **_kwargs: pytest.fail("replay must not call a vendor"),
    )
    context = TemporalContext.at(TemporalMode.REPLAY, datetime.now(UTC), store=TemporalStore(tmp_path))

    with temporal_context(context), pytest.raises(LookupError, match="no eligible evidence"):
        interface.route_to_vendor("get_news", "NVDA", "2025-01-01", "2025-01-02")


def test_direct_source_capture_and_replay_never_call_live_source(tmp_path):
    store = TemporalStore(tmp_path)
    capture = TemporalContext.at(TemporalMode.LIVE_CAPTURE, datetime.now(UTC), store=store)
    request = {"ticker": "NVDA", "limit": 30}
    calls = 0

    def live_source():
        nonlocal calls
        calls += 1
        return "social payload"

    with temporal_context(capture):
        assert invoke_tool("social.stocktwits", request, live_source) == "social payload"

    evidence = store.latest_eligible("social.stocktwits", request, as_of=datetime.now(UTC))
    assert evidence is not None
    replay = TemporalContext.at(
        TemporalMode.REPLAY,
        evidence.available_at + timedelta(microseconds=1),
        store=store,
    )
    with temporal_context(replay):
        assert invoke_tool("social.stocktwits", request, lambda: pytest.fail("must not go live")) == "social payload"
    assert calls == 1


def test_propagate_exposes_the_opt_in_temporal_context(tmp_path):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"checkpoint_enabled": False}
    graph._checkpointer_ctx = None
    graph._resolve_pending_entries = lambda _ticker: None
    graph._run_graph = lambda *_args, **_kwargs: current_context()
    context = TemporalContext.at(TemporalMode.REPLAY, datetime.now(UTC), store=TemporalStore(tmp_path))

    active = graph.propagate("NVDA", "2025-01-02", temporal=context)

    assert active is context
    assert current_context() is None


def test_propagate_can_build_a_capture_context_from_normal_config(tmp_path):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {
        "checkpoint_enabled": False,
        "temporal": {"mode": "live_capture", "store": str(tmp_path / "temporal")},
    }
    graph._checkpointer_ctx = None
    graph._resolve_pending_entries = lambda _ticker: None
    graph._run_graph = lambda *_args, **_kwargs: current_context()

    active = graph.propagate("NVDA", "2025-01-02")

    assert active is not None
    assert active.mode is TemporalMode.LIVE_CAPTURE
    assert active.store is not None and active.store.root == (tmp_path / "temporal")
    assert current_context() is None


def test_configured_replay_uses_the_sealed_scenario_clock(tmp_path):
    store = TemporalStore(tmp_path / "temporal")
    store.seal_scenario(
        "nvda-history",
        as_of=datetime(2025, 1, 2, 16, tzinfo=UTC),
        basis="archive-reconstructed",
        metadata={"ticker": "NVDA", "trade_date": "2025-01-02"},
    )
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {
        "checkpoint_enabled": False,
        "temporal": {
            "mode": "replay",
            "store": str(store.root),
            "scenario_id": "nvda-history",
        },
    }
    graph._checkpointer_ctx = None
    graph._resolve_pending_entries = lambda *_args: pytest.fail("replay must not read memory")
    graph._run_graph = lambda *_args, **_kwargs: current_context()

    active = graph.propagate("NVDA", "2025-01-02")

    assert active is not None
    assert active.mode is TemporalMode.REPLAY
    assert active.scenario_id == "nvda-history"
    assert active.clock.as_of == datetime(2025, 1, 2, 16, tzinfo=UTC)
