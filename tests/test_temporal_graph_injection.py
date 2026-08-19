from unittest.mock import MagicMock, patch

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph


@patch("tradingagents.graph.trading_graph.create_llm_client")
def test_graph_accepts_prebuilt_models_without_creating_provider_clients(mock_factory):
    quick = MagicMock()
    deep = MagicMock()

    graph = TradingAgentsGraph(quick_thinking_llm=quick, deep_thinking_llm=deep)

    assert graph.quick_thinking_llm is quick
    assert graph.deep_thinking_llm is deep
    mock_factory.assert_not_called()


def test_graph_rejects_only_one_prebuilt_model():
    with pytest.raises(ValueError, match="both quick_thinking_llm"):
        TradingAgentsGraph(quick_thinking_llm=MagicMock())
