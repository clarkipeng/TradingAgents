"""Small deterministic portfolio simulator, intentionally separate from evidence retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class InsufficientBuyingPowerError(ValueError):
    """Raised when an order violates the initial cash-only account constraints."""


class MarketTimingError(ValueError):
    """Raised when a proposed fill uses a quote unavailable at its fill time."""


@dataclass(frozen=True)
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    submitted_at: datetime


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    filled_at: datetime


@dataclass(frozen=True)
class MarketQuote:
    """One caller-supplied executable quote with its public availability time."""

    symbol: str
    price: Decimal
    available_at: datetime


@dataclass
class PortfolioState:
    cash: Decimal
    positions: dict[str, Decimal] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)


class PortfolioSimulator:
    """Cash-only deterministic fills at an explicitly supplied market price.

    The caller chooses market-data timing and fill policy. This class applies
    only cost, position, and cash accounting, so it cannot introduce lookahead
    by reaching into the evidence/search layer.
    """

    def __init__(self, initial_cash: Decimal | str | float, *, fee_bps: Decimal | str | float = 0, slippage_bps: Decimal | str | float = 0):
        self.fee_bps = _decimal(fee_bps)
        self.slippage_bps = _decimal(slippage_bps)
        self.state = PortfolioState(cash=_decimal(initial_cash))

    def fill(self, order: Order, *, market_price: Decimal | str | float, filled_at: datetime) -> Fill:
        """Fill an order at the caller-supplied price plus configured slippage and fees."""
        if order.submitted_at.tzinfo is None or filled_at.tzinfo is None:
            raise MarketTimingError("submitted_at and filled_at must be timezone-aware")
        if filled_at < order.submitted_at:
            raise MarketTimingError("a fill cannot precede order submission")
        if order.quantity <= 0:
            raise ValueError("order quantity must be positive")
        price = _decimal(market_price)
        if price <= 0:
            raise ValueError("market price must be positive")
        multiplier = Decimal("1") + (self.slippage_bps / Decimal("10000"))
        fill_price = price * multiplier if order.side is OrderSide.BUY else price / multiplier
        gross = fill_price * order.quantity
        fee = gross * self.fee_bps / Decimal("10000")
        position = self.state.positions.get(order.symbol, Decimal("0"))

        if order.side is OrderSide.BUY:
            required_cash = gross + fee
            if required_cash > self.state.cash:
                raise InsufficientBuyingPowerError("insufficient cash for buy order")
            self.state.cash -= required_cash
            self.state.positions[order.symbol] = position + order.quantity
        else:
            if order.quantity > position:
                raise InsufficientBuyingPowerError("short selling is disabled in the cash-only simulator")
            self.state.cash += gross - fee
            remaining = position - order.quantity
            if remaining:
                self.state.positions[order.symbol] = remaining
            else:
                self.state.positions.pop(order.symbol, None)

        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            fee=fee,
            filled_at=filled_at,
        )
        self.state.fills.append(fill)
        return fill

    def fill_from_quote(self, order: Order, quote: MarketQuote, *, filled_at: datetime) -> Fill:
        """Fill only when the caller's quote was available by the execution time."""
        if quote.symbol != order.symbol:
            raise MarketTimingError("quote symbol does not match order symbol")
        if quote.available_at.tzinfo is None:
            raise MarketTimingError("quote available_at must be timezone-aware")
        if filled_at.tzinfo is None:
            raise MarketTimingError("filled_at must be timezone-aware")
        if quote.available_at > filled_at:
            raise MarketTimingError("quote was not available at the fill time")
        return self.fill(order, market_price=quote.price, filled_at=filled_at)

    def marked_value(self, prices: dict[str, Decimal | str | float]) -> Decimal:
        """Return cash plus marked long positions; prices must come from the caller's time-safe feed."""
        value = self.state.cash
        for symbol, quantity in self.state.positions.items():
            if symbol not in prices:
                raise KeyError(f"missing mark price for {symbol}")
            value += quantity * _decimal(prices[symbol])
        return value


def _decimal(value: Decimal | str | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
