from __future__ import annotations

import copy

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_analysts_only_graph_never_reads_decision_memory(tmp_path):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update({
        "analysts_only": True,
        "max_debate_rounds": 0,
        "max_risk_discuss_rounds": 0,
        "data_cache_dir": str(tmp_path / "cache"),
        "results_dir": str(tmp_path / "results"),
        "memory_log_path": str(tmp_path / "memory.md"),
    })
    graph = TradingAgentsGraph(selected_analysts=("market",), config=config)
    graph.memory_log.get_past_context = lambda *_args: (_ for _ in ()).throw(
        AssertionError("analysts-only run must not read unused memory")
    )
    # Initial-state construction is the boundary that formerly performed the
    # unused read; no provider/model execution is needed to prove it.
    graph._run_graph = graph._run_graph
    from tradingagents.temporal import TemporalContext, TemporalMode, temporal_context
    from datetime import datetime, timezone
    with temporal_context(TemporalContext.at(TemporalMode.LIVE, datetime.now(timezone.utc))):
        # Force only setup by replacing compiled graph invocation.
        graph.graph.invoke = lambda state, **_kwargs: state
        state, signal = graph._run_graph("NVDA", "2026-09-06")
    assert state["past_context"] == ""
    assert signal == "Hold"
