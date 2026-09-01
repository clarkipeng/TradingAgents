"""The daily portfolio loop: sealed state, CIO allocation, rebalance orders.

Each trading day runs research over the universe, asks one CIO call to size
positions under hard constraints, executes the deltas in the simulator at
captured quotes, and seals the resulting state as temporal evidence - so the
next day reads exactly what this day produced, and any day replays exactly.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal

from tradingagents.portfolio_backtest import rating_score, target_weights
from tradingagents.temporal import TemporalStore
from tradingagents.temporal.clock import format_timestamp, parse_timestamp
from tradingagents.temporal.simulation import Order, OrderSide

DEFAULT_CONSTRAINTS = {
    "mode": "long-only",
    "gross_limit": 1.0,
    "max_weight": 0.10,
    "initial_cash": "100000",
}

# A portfolio day is deliberately bounded before any provider work starts.
# Research is one logical call per ticker and CIO sizing is one additional call.
MAX_LLM_CALLS_PER_PORTFOLIO_DAY = 25

logger = logging.getLogger(__name__)

_STATE_TOOL = "portfolio.state"


def record_portfolio_state(
    store: TemporalStore,
    state: dict,
    *,
    day: str,
    available_at: datetime,
) -> str:
    """Seal one day's post-fill portfolio state as ordinary temporal evidence."""
    record = store.record(
        _STATE_TOOL,
        {"portfolio_day": day},
        {
            "cash": str(state["cash"]),
            "positions": {symbol: str(qty) for symbol, qty in state["positions"].items()},
            "equity": str(state["equity"]) if state.get("equity") is not None else None,
        },
        available_at=available_at,
        source="portfolio-simulator",
    )
    return record.evidence_id


def portfolio_state_asof(
    store: TemporalStore,
    as_of: datetime,
    *,
    initial_cash: str = DEFAULT_CONSTRAINTS["initial_cash"],
) -> dict:
    """Read the latest sealed portfolio state observable at ``as_of``."""
    return store.portfolio_state_asof(as_of, initial_cash=initial_cash)


def cio_allocate(
    ratings: dict[str, str],
    *,
    briefs: dict[str, str],
    constraints: dict,
    complete_llm,
) -> dict:
    """Size positions with one CIO call, or deterministically when it misbehaves.

    The LLM proposes weights under hard constraints it cannot relax; any
    violation (unknown ticker, weight over cap, gross over limit, unparseable
    output) rejects the whole proposal and the deterministic rating allocator
    takes over, with the reason recorded in the plan. Money never moves on an
    unvalidated model output.
    """
    prompt = json.dumps({
        "task": (
            "You are the chief investment officer. Propose long-only target"
            " portfolio weights for the next session given the research below."
            " Respond with JSON only: {\"weights\": {ticker: fraction},"
            " \"rationale\": {ticker: reason with [evidence:<id>] citations}}."
        ),
        "constraints": {
            "max_weight": constraints["max_weight"],
            "gross_limit": constraints["gross_limit"],
            "long_only": True,
        },
        "ratings": ratings,
        "briefs": briefs,
    }, ensure_ascii=False, sort_keys=True)

    def fallback(reason: str) -> dict:
        weights = target_weights(
            {ticker: rating_score(rating) for ticker, rating in ratings.items()},
            mode=constraints["mode"],
            gross_limit=constraints["gross_limit"],
            max_weight=constraints["max_weight"],
        )
        return {
            "source": "deterministic-fallback",
            "fallback_reason": reason,
            "weights": {t: w for t, w in weights.items() if w > 0},
            "rationale": {},
        }

    try:
        raw = complete_llm(prompt).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.index("{"):raw.rindex("}") + 1]
        proposal = json.loads(raw)
        weights = proposal["weights"]
        if not isinstance(weights, dict) or not weights:
            return fallback("proposal has no weights mapping")
        validated: dict[str, float] = {}
        for ticker, weight in weights.items():
            if ticker not in ratings:
                return fallback(f"unknown ticker {ticker!r} in proposal")
            value = float(weight)
            if not math.isfinite(value) or value < 0:
                return fallback(f"invalid weight for {ticker}")
            if value > constraints["max_weight"] + 1e-9:
                return fallback(f"weight for {ticker} exceeds max_weight")
            if value > 0:
                validated[ticker] = value
        if sum(validated.values()) > constraints["gross_limit"] + 1e-9:
            return fallback("gross exposure exceeds gross_limit")
        rationale = proposal.get("rationale")
        return {
            "source": "cio-llm",
            "weights": validated,
            "rationale": rationale if isinstance(rationale, dict) else {},
        }
    except Exception as error:  # noqa: BLE001 - any malformed output falls back
        return fallback(f"proposal rejected ({type(error).__name__})")


def run_portfolio_day(
    store: TemporalStore,
    tickers: list[str],
    *,
    day: str,
    research_fn,
    complete_llm,
    quote_fn,
    constraints: dict = DEFAULT_CONSTRAINTS,
    fee_bps: float = 1,
    slippage_bps: float = 2,
    run_id: str | None = None,
) -> dict:
    """One sealed portfolio day: sweep, CIO sizing, simulated fills, sealed state.

    Everything runs under one live-capture temporal context so every lookup
    and model call is taped; the day seals as scenario ``portfolio-<day>``
    and the post-fill state becomes evidence the next day reads. One ticker's
    research failure costs that ticker, never the day.
    """
    import uuid

    from tradingagents.temporal import TemporalContext, TemporalMode, temporal_context
    from tradingagents.temporal.simulation import MarketQuote, PortfolioSimulator

    scenario_id = f"portfolio-{day}"
    if store.get_scenario(scenario_id) is not None:
        # Sealed days are immutable; a retry (manual overlap, a scheduler
        # re-fire after machine sleep) is a quiet no-op, never a crash and
        # never a second spend.
        return {"skipped": "day already sealed", "scenario_id": scenario_id, "day": day}

    planned_llm_calls = len(tickers) + 1
    if planned_llm_calls > MAX_LLM_CALLS_PER_PORTFOLIO_DAY:
        logger.warning(
            "LLM call budget exceeded for portfolio day %s: planned=%d cap=%d; skipping",
            day, planned_llm_calls, MAX_LLM_CALLS_PER_PORTFOLIO_DAY,
        )
        return {
            "skipped": "LLM call budget exceeded",
            "scenario_id": scenario_id,
            "day": day,
            "llm_call_budget": MAX_LLM_CALLS_PER_PORTFOLIO_DAY,
            "planned_llm_calls": planned_llm_calls,
        }

    as_of = parse_timestamp(f"{day}T21:30:00Z")
    context = TemporalContext.at(
        TemporalMode.LIVE_CAPTURE, as_of, store=store,
        run_id=run_id or str(uuid.uuid4()),
    )
    with temporal_context(context):
        llm_calls = 0

        def call_llm(callable_, *args):
            nonlocal llm_calls
            if llm_calls >= MAX_LLM_CALLS_PER_PORTFOLIO_DAY:
                logger.warning(
                    "LLM call budget exceeded for portfolio day %s: calls=%d cap=%d; skipping",
                    day, llm_calls, MAX_LLM_CALLS_PER_PORTFOLIO_DAY,
                )
                return None
            llm_calls += 1
            return callable_(*args)

        ratings: dict[str, str] = {}
        briefs: dict[str, str] = {}
        failures: list[str] = []
        for ticker in tickers:
            try:
                research = call_llm(research_fn, ticker, context)
                if research is None:
                    return {
                        "skipped": "LLM call budget exceeded",
                        "scenario_id": scenario_id,
                        "day": day,
                        "llm_call_budget": MAX_LLM_CALLS_PER_PORTFOLIO_DAY,
                    }
                ratings[ticker] = research["rating"]
                briefs[ticker] = research.get("brief", "")
            except Exception as error:  # noqa: BLE001 - one ticker never kills the day
                failures.append(f"{ticker}: {type(error).__name__}")

        prior = portfolio_state_asof(
            store, as_of, initial_cash=constraints["initial_cash"]
        )
        symbols = sorted(set(ratings) | set(prior["positions"]))
        quotes: dict[str, Decimal] = {}
        for symbol in symbols:
            price = quote_fn(symbol, day)
            if price is None:
                failures.append(f"{symbol}: no quote")
            else:
                quotes[symbol] = Decimal(str(price))

        if not quotes:
            logger.warning(
                "all quotes missing for portfolio day %s; skipping without sealing",
                day,
            )
            return {
                "skipped": "all quotes missing",
                "scenario_id": scenario_id,
                "day": day,
                "failures": failures,
            }

        plan = cio_allocate(
            {t: r for t, r in ratings.items() if t in quotes},
            briefs=briefs, constraints=constraints,
            complete_llm=lambda prompt: call_llm(complete_llm, prompt),
        )
        orders = rebalance_orders(prior, plan["weights"], quotes, submitted_at=as_of)

        simulator = PortfolioSimulator(
            prior["cash"], fee_bps=fee_bps, slippage_bps=slippage_bps
        )
        simulator.state.positions.update({
            symbol: Decimal(str(qty)) for symbol, qty in prior["positions"].items()
        })
        fills = []
        for order in orders:
            quote = MarketQuote(order.symbol, quotes[order.symbol], as_of)
            try:
                fill = simulator.fill_from_quote(order, quote, filled_at=as_of)
            except Exception as error:  # noqa: BLE001 - skip infeasible, keep the day
                failures.append(f"{order.symbol}: {type(error).__name__}")
                continue
            fills.append({
                "symbol": fill.symbol,
                "side": fill.side.value,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
            })

        marked = {s: q for s, q in quotes.items() if s in simulator.state.positions}
        equity = simulator.marked_value(marked)
        state_evidence_id = record_portfolio_state(
            store,
            {
                "cash": simulator.state.cash,
                "positions": {
                    symbol: qty
                    for symbol, qty in simulator.state.positions.items()
                    if qty
                },
                "equity": equity,
            },
            day=day,
            available_at=as_of,
        )

    store.seal_scenario(
        scenario_id,
        as_of=as_of,
        basis="forward-captured",
        metadata={"kind": "portfolio-day", "trade_date": day, "tickers": tickers},
        capture_run_id=context.run_id,
    )
    summary = {
        "day": day,
        "scenario_id": scenario_id,
        "run_id": context.run_id,
        "ratings": ratings,
        "plan": plan,
        "fills": fills,
        "equity": str(equity),
        "cash": str(simulator.state.cash),
        "state_evidence_id": state_evidence_id,
        "failures": failures,
    }
    store.record_research_run(
        context.run_id or "", scenario_id,
        decision=json.dumps(plan, sort_keys=True),
        report=json.dumps(summary, sort_keys=True),
    )
    return summary


def portfolio_report(
    store: TemporalStore,
    *,
    benchmark_closes: dict[str, float] | None = None,
) -> dict:
    """Summarize every sealed portfolio day: equity path, returns, positions.

    Reads only sealed state evidence, so the report is identical whenever it
    is generated. Benchmark closes are caller-supplied (first and last day
    are enough) because the report itself must not fetch anything.
    """
    by_day: dict[str, dict] = {}
    for state in store.portfolio_states():
        by_day[state["portfolio_day"]] = state

    days = []
    previous_equity: Decimal | None = None
    for day in sorted(by_day):
        state = by_day[day]
        equity = Decimal(str(state["equity"])) if state.get("equity") else None
        daily = (
            float((equity - previous_equity) / previous_equity * 100)
            if equity is not None and previous_equity
            else None
        )
        days.append({
            "day": day,
            "equity": state.get("equity"),
            "cash": state.get("cash"),
            "position_count": len(state.get("positions", {})),
            "daily_return_pct": daily,
        })
        if equity is not None:
            previous_equity = equity

    total_return = None
    first = next((d for d in days if d["equity"]), None)
    last = next((d for d in reversed(days) if d["equity"]), None)
    if first and last and first is not last:
        start = Decimal(str(first["equity"]))
        total_return = float((Decimal(str(last["equity"])) - start) / start * 100)

    benchmark_return = None
    if benchmark_closes and first and last:
        start_close = benchmark_closes.get(first["day"])
        end_close = benchmark_closes.get(last["day"])
        if start_close and end_close:
            benchmark_return = (end_close - start_close) / start_close * 100

    latest_state = by_day[sorted(by_day)[-1]] if by_day else None
    return {
        "days": days,
        "total_return_pct": total_return,
        "benchmark_return_pct": benchmark_return,
        "latest": {
            "positions": latest_state.get("positions", {}),
            "cash": latest_state.get("cash"),
            "equity": latest_state.get("equity"),
        } if latest_state else None,
    }


def production_day_inputs(store: TemporalStore, *, deep_model: str = "gpt-5.4",
                          sweep_model: str = "gpt-5.4-mini") -> dict:
    """Real research, CIO, and quote callables for one captured portfolio day.

    The sweep runs the full agent graph per ticker on the quick model with
    single debate rounds; the CIO is one deep-model call. Every LLM call is
    taped and every quote goes through the temporal gateway, so the produced
    day replays exactly.
    """
    import copy

    from tradingagents.agents.utils.rating import parse_rating
    from tradingagents.dataflows.interface import route_to_vendor
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.llm_clients.factory import create_llm_client
    from tradingagents.temporal_adapters.langchain import LangChainTapeRecorder

    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update({
        "llm_provider": "openai",
        "deep_think_llm": sweep_model,
        "quick_think_llm": sweep_model,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "checkpoint_enabled": False,
    })
    config["temporal"] = {
        **DEFAULT_CONFIG["temporal"],
        "mode": "live_capture",
        "store": str(store.root),
        "search_enabled": True,
    }
    recorder = LangChainTapeRecorder(store)
    graph = TradingAgentsGraph(config=config, callbacks=[recorder])
    cio_model = create_llm_client(
        "openai", deep_model, callbacks=[recorder]
    ).get_llm()

    def research(ticker: str, context) -> dict:
        final_state, _signal = graph.propagate(
            ticker, context.clock.as_of.date().isoformat(), temporal=context
        )
        decision = final_state.get("final_trade_decision") or ""
        return {"rating": parse_rating(decision), "brief": decision[:400]}

    def complete(prompt: str) -> str:
        return cio_model.invoke(prompt).content

    def quote(symbol: str, day: str):
        start = format_timestamp(
            parse_timestamp(f"{day}T00:00:00Z") - timedelta(days=7)
        )[:10]
        payload = route_to_vendor("get_stock_data", symbol, start, day)
        return _last_close(payload)

    return {"research_fn": research, "complete_llm": complete, "quote_fn": quote}


def _last_close(payload) -> Decimal | None:
    """Pull the final Close from a get_stock_data CSV payload, or None."""
    if not isinstance(payload, str) or payload.startswith("NO_DATA_AVAILABLE"):
        return None
    lines = [line for line in payload.strip().splitlines() if "," in line]
    if len(lines) < 2:
        return None
    header = [column.strip().lower() for column in lines[0].split(",")]
    if "close" not in header:
        return None
    index = header.index("close")
    for line in reversed(lines[1:]):
        fields = line.split(",")
        if len(fields) > index:
            try:
                return Decimal(fields[index].strip())
            except Exception:  # noqa: BLE001 - malformed rows are skipped
                continue
    return None


def rebalance_orders(
    state: dict,
    weights: dict[str, float],
    quotes: dict[str, Decimal],
    *,
    submitted_at: datetime,
) -> list[Order]:
    """Whole-share orders moving the portfolio to its target weights.

    Sells are emitted before buys so freed cash funds the purchases; buy
    quantities floor to whole shares so the plan can never overdraw.
    """
    cash = Decimal(str(state["cash"]))
    positions = {symbol: Decimal(str(qty)) for symbol, qty in state["positions"].items()}
    equity = cash + sum(
        qty * quotes[symbol] for symbol, qty in positions.items() if symbol in quotes
    )

    deltas: list[tuple[str, Decimal]] = []
    for symbol in sorted(set(positions) | set(weights)):
        if symbol not in quotes:
            continue
        target_value = equity * Decimal(str(weights.get(symbol, 0.0)))
        target_shares = Decimal(int(target_value / quotes[symbol]))
        change = target_shares - positions.get(symbol, Decimal("0"))
        if change:
            deltas.append((symbol, change))

    orders = []
    for sequence, (symbol, change) in enumerate(
        sorted(deltas, key=lambda item: item[1] > 0), start=1
    ):
        side = OrderSide.BUY if change > 0 else OrderSide.SELL
        orders.append(Order(
            f"rebalance-{submitted_at:%Y%m%d}-{sequence}",
            symbol,
            side,
            abs(change),
            submitted_at,
        ))
    return orders
