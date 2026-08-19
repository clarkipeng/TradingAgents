import copy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeListChatModel

import tradingagents.agents.analysts.sentiment_analyst as sentiment
import tradingagents.dataflows.interface as interface
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.temporal import TemporalContext, TemporalMode, TemporalStore
from tradingagents.temporal_adapters.langchain import LangChainTapeRecorder, TapeChatModel

UTC = timezone.utc


def _config(root: Path) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(
        {
            "data_cache_dir": str(root / "cache"),
            "results_dir": str(root / "results"),
            "memory_log_path": str(root / "memory.md"),
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
            "data_vendors": {**config["data_vendors"], "news_data": "yfinance"},
        }
    )
    return config


def _graph(config: dict, quick, deep) -> TradingAgentsGraph:
    graph = TradingAgentsGraph(
        selected_analysts=("social",),
        config=config,
        quick_thinking_llm=quick,
        deep_thinking_llm=deep,
    )
    graph.resolve_instrument_context = lambda *_args: ""
    return graph


def test_full_trace_replay_runs_graph_without_sources_or_model_provider(tmp_path):
    store = TemporalStore(tmp_path / "tape")
    recorder = LangChainTapeRecorder(store)
    captured_model = FakeListChatModel(responses=["analysis: HOLD"], callbacks=[recorder])
    captured = _graph(_config(tmp_path), captured_model, captured_model)
    captured.memory_log.get_past_context = lambda *_args: "captured prior research"
    capture_context = TemporalContext.at(
        TemporalMode.LIVE_CAPTURE,
        datetime.now(UTC),
        scenario_id="golden",
        store=store,
    )

    with (
        patch.dict(
            interface.VENDOR_METHODS,
            {"get_news": {"yfinance": lambda *_args: "news"}},
            clear=False,
        ),
        patch.object(sentiment, "fetch_stocktwits_messages", lambda *_args, **_kwargs: "stocktwits"),
        patch.object(sentiment, "fetch_reddit_posts", lambda *_args, **_kwargs: "reddit"),
    ):
        captured_state, _ = captured.propagate("NVDA", "2025-01-02", temporal=capture_context)
    captured_memory = (tmp_path / "memory.md").read_text(encoding="utf-8")
    store.seal_scenario(
        "golden",
        as_of=capture_context.clock.as_of,
        basis="forward-captured",
        metadata={"ticker": "NVDA", "trade_date": "2025-01-02"},
        capture_run_id=capture_context.run_id,
    )

    snapshot = store.get_scenario_snapshot("golden", "tradingagents.memory_context")
    assert snapshot is not None and snapshot.state == "captured prior research"
    tape = TapeChatModel.from_scenario(store, "golden")
    # Replay blocks ambient decision-memory input and prompt verification stays
    # enabled, ignoring only LangChain's fresh per-message UUIDs.
    replay = _graph(_config(tmp_path), tape, tape)

    def forbidden_memory(*_args, **_kwargs):
        raise AssertionError("full-trace replay must use the sealed scenario snapshot")

    replay.memory_log.get_past_context = forbidden_memory
    replay_context = TemporalContext.from_scenario(
        TemporalMode.REPLAY,
        store,
        "golden",
        use_capture_tape=True,
    )
    # A newer time-eligible response must not replace an exact full-trace tool result.
    store.record(
        "dataflow.get_news",
        {"args": ["NVDA", "2024-12-26", "2025-01-02"], "kwargs": {}},
        "newer news",
        available_at=replay_context.clock.as_of,
    )

    def forbidden_source(*_args, **_kwargs):
        raise AssertionError("full-trace replay must not contact an external source")

    with (
        patch.dict(interface.VENDOR_METHODS, {"get_news": {"yfinance": forbidden_source}}, clear=False),
        patch.object(sentiment, "fetch_stocktwits_messages", forbidden_source),
        patch.object(sentiment, "fetch_reddit_posts", forbidden_source),
    ):
        replayed_state, _ = replay.propagate("NVDA", "2025-01-02", temporal=replay_context)

    assert replayed_state["final_trade_decision"] == captured_state["final_trade_decision"]
    assert (tmp_path / "memory.md").read_text(encoding="utf-8") == captured_memory
    assert len(store.list_llm_calls("golden")) > 0
    assert len(store.list_tool_traces(capture_context.run_id)) == 3
    assert len(store.list_tool_traces(replay_context.run_id)) == 3
