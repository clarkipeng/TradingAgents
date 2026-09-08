from datetime import datetime, timezone
from decimal import Decimal

from tradingagents.portfolio_run import run_portfolio_day
from tradingagents.temporal import TemporalGateway, TemporalStore


def test_historical_portfolio_day_uses_eligible_evidence_without_provider_access(tmp_path):
    store = TemporalStore(tmp_path)
    store.record(
        "fixture.research", {"ticker": "NVDA"}, {"rating": "Buy"},
        available_at=datetime(2025, 1, 2, 20, tzinfo=timezone.utc),
    )
    gateway = TemporalGateway(store)
    provider_calls = []

    def research(ticker, context):
        outcome = gateway.invoke(
            "fixture.research", {"ticker": ticker}, context,
            live_call=lambda: provider_calls.append(ticker),
        )
        return {"rating": outcome.value["rating"], "brief": "captured fixture"}

    result = run_portfolio_day(
        store, ["NVDA"], day="2025-01-02", research_fn=research,
        complete_llm=lambda _: '{"weights": {"NVDA": 0.1}}',
        quote_fn=lambda *_: Decimal("100"),
    )

    assert result["plan"]["source"] == "cio-llm"
    assert provider_calls == []
