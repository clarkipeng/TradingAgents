"""A provider failure must not be interpreted as a hold-book proposal."""
from decimal import Decimal

from tradingagents import portfolio_run
from tradingagents.temporal import TemporalStore


def test_cio_provider_failure_cannot_seal_a_hold_day(tmp_path):
    def unavailable(_prompt):
        raise RuntimeError("provider unavailable")

    with TemporalStore(tmp_path) as store:
        result = portfolio_run.run_portfolio_day(
            store, ["NVDA"], day="2026-08-28",
            research_fn=lambda ticker, context: {"rating": "Buy", "brief": "fixture"},
            complete_llm=unavailable,
            quote_fn=lambda ticker, day: Decimal("100"),
        )
        assert result["status"] == "failed_unsealed"
        assert result["reason"] == "execution_error"
        assert store.get_scenario("portfolio-2026-08-28") is None
        assert store.portfolio_day("2026-08-28")["status"] == "failed"
