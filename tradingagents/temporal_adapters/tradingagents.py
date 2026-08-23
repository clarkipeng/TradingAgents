"""Bridge TradingAgents dataflow calls into the temporal core without changing tool shapes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from tradingagents.temporal import (
    FactualClaim,
    ReplayMissError,
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

    try:
        outcome = TemporalGateway(context.store).invoke(
            tool,
            request,
            context,
            live_call,
        )
    except ReplayMissError:
        # Evidence replay degrades like live vendor exhaustion: the agent sees
        # the repo-wide NO_DATA_AVAILABLE sentinel and adapts, instead of one
        # improvised tool argument aborting the whole run. Full-tape replay
        # keeps its strict tape-mismatch failure.
        return (
            f"NO_DATA_AVAILABLE: no evidence for {tool} was captured at or "
            f"before {context.clock.as_of.isoformat()}. This source cannot "
            "answer this exact request in replay; rely on other evidence."
        )
    return outcome.value


def capture_daily_market_research(
    store: TemporalStore,
    tickers: Iterable[str],
    *,
    now: datetime | None = None,
    news_lookback_days: int = 7,
    full_surface: bool = False,
) -> DailyCaptureResult:
    """Capture existing tool surfaces once; a scheduler can invoke this daily.

    Source errors are persisted by the tool tape and reported here while the
    rest of the ticker universe continues. ``full_surface`` adds statements,
    insiders, global news, a small macro basket, and prediction markets; it is
    opt-in so the original fast daily capture remains unchanged.
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

    from tradingagents.dataflows.hacker_news import fetch_hacker_news_stories
    from tradingagents.dataflows.interface import route_to_vendor
    from tradingagents.dataflows.reddit import fetch_reddit_posts
    from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages

    def record_capture(scope: str, name: str, call: Callable[[], Any]) -> None:
        nonlocal attempted, completed
        attempted += 1
        try:
            call()
        except Exception as error:
            # Preserve the source failure in the operator summary even when
            # the normal vendor router wraps it in VendorError.
            root_error = error.__cause__ or error
            failures.append(f"{scope}:{name}:{type(root_error).__name__}")
        else:
            completed += 1

    with temporal_context(context), store.write_lock():
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
            for name, call in captures:
                record_capture(ticker, name, call)
            if full_surface:
                for name, call in (
                    ("get_fundamentals", lambda ticker=ticker: route_to_vendor("get_fundamentals", ticker, end_date)),
                    ("get_balance_sheet", lambda ticker=ticker: route_to_vendor("get_balance_sheet", ticker, "quarterly", end_date)),
                    ("get_cashflow", lambda ticker=ticker: route_to_vendor("get_cashflow", ticker, "quarterly", end_date)),
                    ("get_income_statement", lambda ticker=ticker: route_to_vendor("get_income_statement", ticker, "quarterly", end_date)),
                    ("get_insider_transactions", lambda ticker=ticker: route_to_vendor("get_insider_transactions", ticker)),
                ):
                    record_capture(ticker, name, call)
        # Hacker News is a global feed, so collect it once per capture run rather
        # than redundantly once per ticker. Its immutable tape entry remains
        # available to every scenario created from this run.
        def capture_hacker_news() -> None:
            hacker_news_request = {"feed": "top", "limit": 8}
            hacker_news_rows = invoke_tool(
                "social.hackernews",
                hacker_news_request,
                lambda: fetch_hacker_news_stories(
                    "top", captured_at.timestamp(), limit=8
                ),
            )
            _record_hacker_news_documents(
                store,
                rows=hacker_news_rows,
                request=hacker_news_request,
                captured_at=captured_at,
            )
        record_capture("global", "hacker_news", capture_hacker_news)
        if full_surface:
            for name, call in (
                ("get_global_news", lambda: route_to_vendor("get_global_news", end_date, 7, 20)),
                ("macro_cpi", lambda: route_to_vendor("get_macro_indicators", "cpi", end_date, 365)),
                ("macro_unemployment", lambda: route_to_vendor("get_macro_indicators", "unemployment", end_date, 365)),
                ("macro_fed_funds_rate", lambda: route_to_vendor("get_macro_indicators", "fed_funds_rate", end_date, 365)),
                ("get_prediction_markets", lambda: route_to_vendor("get_prediction_markets", "US equities", 6)),
            ):
                record_capture("global", name, call)
    return DailyCaptureResult(
        attempted=attempted,
        completed=completed,
        failures=tuple(failures),
        run_id=context.run_id or "",
        captured_at=context.clock.as_of,
        start_date=start_date,
        end_date=end_date,
    )


def _record_hacker_news_documents(
    store: TemporalStore,
    *,
    rows: Any,
    request: Mapping[str, Any],
    captured_at: datetime,
) -> None:
    """Derive searchable HN story documents from one replay-tape response.

    The complete feed response remains the exact tool-replay unit. Each valid
    story is additionally a small ``corpus.document`` record with its own
    publication clock and the parent artifact hash in metadata, so FTS can rank
    posts rather than a whole feed blob.
    """
    parent = store.latest_eligible("social.hackernews", request, as_of=captured_at)
    if parent is None or not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        external_id = row.get("external_id")
        title = row.get("title")
        published_epoch = row.get("created_utc")
        metadata = row.get("metadata")
        if (
            not isinstance(external_id, str)
            or not isinstance(title, str)
            or isinstance(published_epoch, bool)
            or not isinstance(published_epoch, (int, float))
        ):
            continue
        published_at = datetime.fromtimestamp(published_epoch, timezone.utc)
        source = (
            metadata.get("discussion_url")
            if isinstance(metadata, Mapping) and isinstance(metadata.get("discussion_url"), str)
            else f"https://news.ycombinator.com/item?id={external_id}"
        )
        store.record(
            "corpus.document",
            {
                "source": "hacker-news-forward",
                "external_id": external_id,
                "parent_artifact_hash": parent.artifact_hash,
            },
            {
                "text": f"{title}\n\n{row.get('body', '')}".strip(),
                "metadata": {
                    "source_record": dict(row),
                    "parent_artifact_hash": parent.artifact_hash,
                    "availability_basis": "hn-story-created_utc",
                },
            },
            # A story's timestamp is preserved below, but it enters our owned
            # search corpus only once this poller observed the feed.
            available_at=captured_at,
            observed_at=captured_at,
            event_at=published_at,
            source_published_at=published_at,
            fidelity="forward-captured",
            source=source,
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
    store.record_research_run(
        context.run_id or "",
        scenario_id,
        decision=decision,
        report=decision,
    )
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
