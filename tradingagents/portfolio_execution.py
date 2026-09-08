"""Versioned next-session execution for newly captured portfolio targets.

Legacy ``close-legacy-v1`` seals remain verifiable in ``portfolio_replay``.
New callers may use this module to keep the decision and executable opening
quote on different, explicit timestamps.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradingagents.temporal.simulation import MarketQuote, PortfolioSimulator

NEXT_OPEN_EXECUTION_POLICY = "next-open-v1"


@dataclass(frozen=True)
class ExecutionQuote:
    """An opening quote that is eligible for a particular execution session."""

    symbol: str
    session: str
    price: Decimal
    available_at: datetime
    adjustment_basis: str
    evidence_id: str
    split_ratio: Decimal = Decimal("1")
    cash_dividend: Decimal = Decimal("0")

    def market_quote(self) -> MarketQuote:
        if self.available_at.tzinfo is None:
            raise ValueError("execution quote availability must be timezone-aware")
        if not self.evidence_id or self.adjustment_basis != "raw-ohlcv-v1":
            raise ValueError("execution quote lacks a versioned raw evidence basis")
        if self.price <= 0 or not self.price.is_finite():
            raise ValueError("execution quote price is invalid")
        if self.split_ratio <= 0 or not self.split_ratio.is_finite():
            raise ValueError("execution quote split ratio is invalid")
        if self.cash_dividend < 0 or not self.cash_dividend.is_finite():
            raise ValueError("execution quote dividend is invalid")
        return MarketQuote(self.symbol, self.price, self.available_at)


@dataclass(frozen=True)
class ExecutionTiming:
    """Decision and fill times for one after-close target."""

    decision_ready_at: datetime
    execution_session: str
    submitted_at: datetime
    filled_at: datetime


def next_open_timing(decision_ready_at: datetime) -> ExecutionTiming:
    """Choose the first XNYS open strictly after the actual ready instant."""
    if decision_ready_at.tzinfo is None:
        raise ValueError("decision_ready_at must be timezone-aware")
    from tradingagents.research.timeline import _xnys_calendar

    ready = decision_ready_at.astimezone(timezone.utc)
    calendar = _xnys_calendar()
    # UTC calendar date is not an exchange session decision date: 00:01Z can
    # still precede that day's New York open. Select by open timestamp, so a
    # weekend, holiday, and just-after-midnight completion all follow one rule.
    start = (ready.date() - timedelta(days=7)).isoformat()
    end = (ready.date() + timedelta(days=14)).isoformat()
    for session in calendar.sessions_in_range(start, end):
        opened = calendar.session_open(session).to_pydatetime().astimezone(timezone.utc)
        if opened > ready:
            return ExecutionTiming(ready, session.date().isoformat(), opened, opened)
    raise ValueError("no eligible XNYS opening session in bounded execution window")


def execute_next_open_targets(
    prior_state: Mapping[str, object],
    plan: Mapping[str, object],
    quotes: Mapping[str, ExecutionQuote],
    *,
    timing: ExecutionTiming,
    fee_bps: Decimal | str | float = 1,
    slippage_bps: Decimal | str | float = 2,
) -> dict[str, object]:
    """Apply a target only with its next-session, evidence-backed raw opens."""
    from tradingagents.portfolio_run import rebalance_orders

    weights = plan.get("weights", {})
    if not isinstance(weights, Mapping):
        raise ValueError("execution target lacks weights")
    required = set(weights) | set(prior_state.get("positions", {}))
    if required - set(quotes):
        raise ValueError("execution quote coverage is incomplete")
    if any(quote.session != timing.execution_session for quote in quotes.values()):
        raise ValueError("execution quotes belong to different sessions")
    if any(quote.available_at > timing.filled_at for quote in quotes.values()):
        raise ValueError("execution quote was unavailable at the eligible opening")
    for quote in quotes.values():
        quote.market_quote()
    if plan.get("hold_book"):
        orders = []
    else:
        prices = {symbol: quote.price for symbol, quote in quotes.items()}
        adjusted_positions = {}
        adjusted_cash = Decimal(str(prior_state["cash"]))
        for symbol, quantity_value in prior_state.get("positions", {}).items():
            quantity = Decimal(str(quantity_value))
            quote = quotes[symbol]
            adjusted_cash += quantity * quote.cash_dividend
            adjusted_positions[symbol] = quantity * quote.split_ratio
        executable_state = {"cash": adjusted_cash, "positions": adjusted_positions}
        orders = rebalance_orders(
            executable_state, weights, prices, submitted_at=timing.submitted_at,
            fee_bps=fee_bps, slippage_bps=slippage_bps,
        )
    if plan.get("hold_book"):
        adjusted_cash = Decimal(str(prior_state["cash"]))
        adjusted_positions = {}
        for symbol, quantity_value in prior_state.get("positions", {}).items():
            quantity = Decimal(str(quantity_value))
            quote = quotes[symbol]
            adjusted_cash += quantity * quote.cash_dividend
            adjusted_positions[symbol] = quantity * quote.split_ratio
    simulator = PortfolioSimulator(prior_state["cash"], fee_bps=fee_bps, slippage_bps=slippage_bps)
    simulator.state.cash = adjusted_cash
    simulator.state.positions.update(adjusted_positions)
    fills = []
    for order in orders:
        quote = quotes[order.symbol]
        fill = simulator.fill_from_quote(order, quote.market_quote(), filled_at=timing.filled_at)
        fills.append({
            "symbol": fill.symbol, "side": fill.side.value,
            "quantity": str(fill.quantity), "price": str(fill.price),
            "evidence_id": quote.evidence_id,
        })
    return {
        "policy": NEXT_OPEN_EXECUTION_POLICY,
        "decision_ready_at": timing.decision_ready_at.isoformat(),
        "execution_session": timing.execution_session,
        "submitted_at": timing.submitted_at.isoformat(),
        "filled_at": timing.filled_at.isoformat(),
        "fills": fills,
        "corporate_actions": {
            symbol: {"split_ratio": str(quote.split_ratio), "cash_dividend": str(quote.cash_dividend)}
            for symbol, quote in sorted(quotes.items())
            if symbol in prior_state.get("positions", {})
        },
        "final_state": {
            "cash": str(simulator.state.cash),
            "positions": {symbol: str(quantity) for symbol, quantity in simulator.state.positions.items() if quantity},
            "equity": str(simulator.marked_value({symbol: quotes[symbol].price for symbol in simulator.state.positions})),
        },
    }
