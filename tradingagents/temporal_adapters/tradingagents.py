"""Bridge TradingAgents dataflow calls into the temporal core without changing tool shapes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from tradingagents.temporal import (
    FactualClaim,
    Fill,
    MarketQuote,
    Order,
    OrderSide,
    PortfolioSimulator,
    ResearchTrace,
    TemporalContext,
    TemporalGateway,
    TemporalMode,
    TemporalStore,
    cited_claims_from_markdown,
    current_context,
    temporal_context,
    trace_from_tool_run,
)


@dataclass(frozen=True)
class DailyCaptureResult:
    attempted: int
    completed: int
    failures: tuple[str, ...]
    run_id: str
    captured_at: datetime
    start_date: str
    end_date: str


@dataclass(frozen=True)
class DecisionExecution:
    """The explicit long-only execution consequence of one TradingAgents decision."""

    rating: str
    order: Order | None
    fill: Fill | None
    skipped_reason: str | None = None


def invoke_dataflow(
    method: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    live_call: Callable[[], Any],
) -> Any:
    """Run a current dataflow call unchanged, capture it, or replay it by context."""
    return invoke_tool(
        f"dataflow.{method}",
        {"args": list(args), "kwargs": dict(kwargs)},
        live_call,
    )


def invoke_tool(tool: str, request: Mapping[str, Any], live_call: Callable[[], Any]) -> Any:
    """Run a non-vendor TradingAgents source through the active temporal context."""
    context = current_context()
    if context is None or context.mode is TemporalMode.LIVE:
        return live_call()
    if context.store is None:
        raise RuntimeError("temporal capture and replay require a TemporalStore")

    outcome = TemporalGateway(context.store).invoke(
        tool,
        request,
        context,
        live_call,
    )
    return outcome.value


def capture_daily_market_research(
    store: TemporalStore,
    tickers: Iterable[str],
    *,
    now: datetime | None = None,
    news_lookback_days: int = 7,
) -> DailyCaptureResult:
    """Capture existing price/news/social tools once; a scheduler can invoke this daily.

    Source errors are persisted by the tool tape and reported here while the
    rest of the ticker universe continues.
    """
    if news_lookback_days < 1:
        raise ValueError("news_lookback_days must be positive")
    captured_at = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    end_date = captured_at.date().isoformat()
    start_date = (captured_at - timedelta(days=news_lookback_days)).date().isoformat()
    context = TemporalContext.at(TemporalMode.LIVE_CAPTURE, captured_at, store=store)
    attempted = completed = 0
    failures: list[str] = []

    from tradingagents.dataflows.interface import route_to_vendor
    from tradingagents.dataflows.reddit import fetch_reddit_posts
    from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages

    with temporal_context(context):
        for ticker in tickers:
            captures = (
                (
                    "get_stock_data",
                    lambda ticker=ticker: route_to_vendor(
                        "get_stock_data", ticker, start_date, end_date
                    ),
                ),
                (
                    "get_news",
                    lambda ticker=ticker: route_to_vendor("get_news", ticker, start_date, end_date),
                ),
                (
                    "stocktwits",
                    lambda ticker=ticker: invoke_tool(
                        "social.stocktwits",
                        {"ticker": ticker, "limit": 30},
                        lambda ticker=ticker: fetch_stocktwits_messages(ticker, limit=30),
                    ),
                ),
                (
                    "reddit",
                    lambda ticker=ticker: invoke_tool(
                        "social.reddit",
                        {"ticker": ticker, "subreddits": "default", "limit_per_sub": 5},
                        lambda ticker=ticker: fetch_reddit_posts(ticker),
                    ),
                ),
            )
            for name, capture in captures:
                attempted += 1
                try:
                    capture()
                except Exception as error:
                    # Preserve the source failure in the operator summary even
                    # when the normal vendor router wraps it in VendorError.
                    root_error = error.__cause__ or error
                    failures.append(f"{ticker}:{name}:{type(root_error).__name__}")
                else:
                    completed += 1
    return DailyCaptureResult(
        attempted=attempted,
        completed=completed,
        failures=tuple(failures),
        run_id=context.run_id or "",
        captured_at=context.clock.as_of,
        start_date=start_date,
        end_date=end_date,
    )


def execute_final_decision(
    simulator: PortfolioSimulator,
    *,
    final_trade_decision: str,
    symbol: str,
    quantity: Decimal,
    order_id: str,
    submitted_at: datetime,
    quote: MarketQuote,
    filled_at: datetime,
) -> DecisionExecution:
    """Map TradingAgents' five-tier rating to a transparent long-only fill policy.

    Buy/Overweight add ``quantity``. Underweight reduces by ``quantity`` and
    Sell exits the existing long position. Hold and an unowned sell position
    deliberately create no order; shorting remains outside the phase-one simulator.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    from tradingagents.agents.utils.rating import parse_rating

    rating = parse_rating(final_trade_decision)
    position = simulator.state.positions.get(symbol, Decimal("0"))
    if rating in {"Buy", "Overweight"}:
        side, order_quantity = OrderSide.BUY, quantity
    elif rating == "Underweight":
        if position <= 0:
            return DecisionExecution(rating, None, None, "no long position to reduce")
        side, order_quantity = OrderSide.SELL, min(quantity, position)
    elif rating == "Sell":
        if position <= 0:
            return DecisionExecution(rating, None, None, "no long position to exit")
        side, order_quantity = OrderSide.SELL, position
    else:
        return DecisionExecution(rating, None, None, "hold")

    order = Order(order_id, symbol, side, order_quantity, submitted_at)
    fill = simulator.fill_from_quote(order, quote, filled_at=filled_at)
    return DecisionExecution(rating, order, fill)


def replay_scenario(
    graph: Any,
    store: TemporalStore,
    scenario_id: str,
    *,
    claims: Iterable[FactualClaim] | None = None,
) -> ResearchTrace:
    """Run a TradingAgents graph against a sealed evidence-replay scenario.

    The scenario metadata supplies ``ticker``, ``trade_date``, and optional
    ``asset_type``. Graph construction remains caller-owned, making this a
    small adapter for prompt/graph A/B experiments rather than another runner.
    """
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise KeyError(f"unknown scenario: {scenario_id}")
    if not store.verify_scenario_corpus(scenario_id):
        raise RuntimeError(f"scenario corpus drift detected: {scenario_id}")
    ticker = scenario.metadata.get("ticker")
    trade_date = scenario.metadata.get("trade_date")
    if not isinstance(ticker, str) or not isinstance(trade_date, str):
        raise ValueError("scenario metadata must include string ticker and trade_date")
    asset_type = scenario.metadata.get("asset_type", "stock")
    if not isinstance(asset_type, str):
        raise ValueError("scenario asset_type must be a string")
    context = TemporalContext.from_scenario(TemporalMode.REPLAY, store, scenario_id)
    final_state, _signal = graph.propagate(
        ticker,
        trade_date,
        asset_type=asset_type,
        temporal=context,
    )
    decision = final_state.get("final_trade_decision")
    return trace_from_tool_run(
        store,
        run_id=context.run_id,
        scenario_id=scenario_id,
        claims=(
            claims
            if claims is not None
            else cited_claims_from_markdown(decision or "", claim_prefix="final-decision")
        ),
        decision=decision,
    )
