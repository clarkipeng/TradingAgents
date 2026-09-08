from tradingagents import portfolio_run
from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_portfolio_policy_bounds_every_model_completion_and_sdk_retries():
    assert portfolio_run.PORTFOLIO_DAY_POLICY["research_max_completion_tokens"] > 0
    assert portfolio_run.PORTFOLIO_DAY_POLICY["cio_max_completion_tokens"] > 0


def test_graph_forwards_positive_completion_cap_and_zero_retries():
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {
        "llm_provider": "openai", "deep_think_llm": "gpt-5", "quick_think_llm": "gpt-5-mini",
        "llm_max_retries": 0, "llm_max_completion_tokens": 64,
    }
    assert graph._get_provider_kwargs() == {"max_retries": 0, "max_completion_tokens": 64}
