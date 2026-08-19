from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingagents.temporal import (
    InsufficientBuyingPowerError,
    MarketQuote,
    MarketTimingError,
    Order,
    OrderSide,
    PortfolioSimulator,
)
from tradingagents.temporal_adapters.tradingagents import execute_final_decision

UTC = timezone.utc


def test_cash_only_simulator_applies_slippage_fees_and_marks_positions():
    simulator = PortfolioSimulator("1000", fee_bps="10", slippage_bps="100")
    order = Order("1", "NVDA", OrderSide.BUY, Decimal("2"), datetime(2025, 1, 2, tzinfo=UTC))

    fill = simulator.fill(order, market_price="100", filled_at=datetime(2025, 1, 2, 10, tzinfo=UTC))

    assert fill.price == Decimal("101.00")
    assert fill.fee == Decimal("0.202")
    assert simulator.state.cash == Decimal("797.798")
    assert simulator.marked_value({"NVDA": "110"}) == Decimal("1017.798")


def test_cash_only_simulator_rejects_oversell_and_overspend():
    simulator = PortfolioSimulator("100")
    buy = Order("buy", "NVDA", OrderSide.BUY, Decimal("2"), datetime(2025, 1, 2, tzinfo=UTC))
    with pytest.raises(InsufficientBuyingPowerError, match="insufficient cash"):
        simulator.fill(buy, market_price="60", filled_at=datetime(2025, 1, 2, 10, tzinfo=UTC))

    sell = Order("sell", "NVDA", OrderSide.SELL, Decimal("1"), datetime(2025, 1, 2, tzinfo=UTC))
    with pytest.raises(InsufficientBuyingPowerError, match="short selling"):
        simulator.fill(sell, market_price="60", filled_at=datetime(2025, 1, 2, 10, tzinfo=UTC))


def test_quote_fill_rejects_future_information_and_adapts_final_decisions():
    simulator = PortfolioSimulator("1000")
    submitted_at = datetime(2025, 1, 2, 10, tzinfo=UTC)
    quote = MarketQuote("NVDA", Decimal("100"), datetime(2025, 1, 2, 10, tzinfo=UTC))

    buy = execute_final_decision(
        simulator,
        final_trade_decision="**Rating**: Buy",
        symbol="NVDA",
        quantity=Decimal("2"),
        order_id="buy-1",
        submitted_at=submitted_at,
        quote=quote,
        filled_at=submitted_at,
    )
    sell = execute_final_decision(
        simulator,
        final_trade_decision="**Rating**: Sell",
        symbol="NVDA",
        quantity=Decimal("1"),
        order_id="sell-1",
        submitted_at=submitted_at,
        quote=quote,
        filled_at=submitted_at,
    )

    assert buy.fill is not None and buy.fill.side is OrderSide.BUY
    assert sell.fill is not None and sell.fill.quantity == Decimal("2")
    assert simulator.state.positions == {}
    future_quote = MarketQuote("NVDA", Decimal("100"), datetime(2025, 1, 2, 11, tzinfo=UTC))
    with pytest.raises(MarketTimingError, match="not available"):
        simulator.fill_from_quote(
            Order("late", "NVDA", OrderSide.BUY, Decimal("1"), submitted_at),
            future_quote,
            filled_at=submitted_at,
        )
