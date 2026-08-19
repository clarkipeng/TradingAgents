"""The market analyst is bound (and prompt-instructed) to call
get_verified_market_snapshot; if the executor ToolNode doesn't register it, the
call fails and the model reports the tool "unavailable" and skips verification.

Regression guard for that wiring gap (snapshot bound to the LLM but missing from
the market ToolNode).
"""
import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.temporal_adapters.langchain import create_contextual_temporal_search_tool


@pytest.mark.unit
def test_market_toolnode_can_execute_verified_snapshot():
    # _create_tool_nodes does not use self -> call unbound (avoids building LLMs).
    nodes = TradingAgentsGraph._create_tool_nodes(None)
    market_tools = set(nodes["market"].tools_by_name)
    assert "get_verified_market_snapshot" in market_tools, (
        "get_verified_market_snapshot is bound to the market analyst but not "
        "registered in the market ToolNode, so the model's call fails."
    )
    # the other core market tools must remain too
    assert {"get_stock_data", "get_indicators"} <= market_tools


@pytest.mark.unit
def test_optional_temporal_search_is_registered_with_each_tool_calling_analyst():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.analyst_extra_tools = (create_contextual_temporal_search_tool(),)

    nodes = graph._create_tool_nodes()

    for analyst in ("market", "news", "fundamentals"):
        assert "temporal_search" in nodes[analyst].tools_by_name
    assert "temporal_search" not in nodes["social"].tools_by_name
