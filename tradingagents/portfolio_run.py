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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradingagents.portfolio_backtest import rating_score, target_weights
from tradingagents.temporal import TemporalRunInvalidError, TemporalStore
from tradingagents.temporal.clock import format_timestamp, parse_timestamp
from tradingagents.temporal.store import PORTFOLIO_CLAIM_STALE_SECONDS
from tradingagents.temporal.simulation import Order, OrderSide

DEFAULT_CONSTRAINTS = {
    "mode": "long-only",
    "gross_limit": 1.0,
    "max_weight": 0.10,
    "initial_cash": "100000",
}

# A portfolio day is deliberately bounded before any provider work starts.
# One unit is one research invocation (a full per-ticker graph run) or the
# CIO sizing call; the 30-ticker universe plus CIO plus headroom fits under it.
MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY = 40
PORTFOLIO_DAY_POLICY = {
    "wall_clock_seconds": 900,
    "minimum_ticker_coverage": 0.8,
    "max_workers": 4,
    "complete_held_quote_coverage": True,
}
MAX_RESEARCH_WORKERS = PORTFOLIO_DAY_POLICY["max_workers"]
MIN_TICKER_COVERAGE = PORTFOLIO_DAY_POLICY["minimum_ticker_coverage"]
PORTFOLIO_DAY_DEADLINE_SECONDS = PORTFOLIO_DAY_POLICY["wall_clock_seconds"]

logger = logging.getLogger(__name__)

_STATE_TOOL = "portfolio.state"


def record_portfolio_state(
    store: TemporalStore,
    state: dict,
    *,
    day: str,
    available_at: datetime,
) -> str:
    """Capture a fixture record without making it a visible portfolio day.

    Production completion is owned by ``complete_portfolio_day``.  This narrow
    helper remains for old import fixtures, but projections deliberately ignore
    its evidence until a completed claim exists.
    """
    return store.record(
        _STATE_TOOL,
        {"portfolio_day": day},
        {
            "cash": str(state["cash"]),
            "positions": {symbol: str(qty) for symbol, qty in state["positions"].items()},
            "equity": str(state["equity"]) if state.get("equity") is not None else None,
        },
        available_at=available_at,
        source="portfolio-simulator",
    ).evidence_id


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


def run_portfolio_day(store: TemporalStore, tickers: list[str], **kwargs) -> dict:
    """Claim, execute, and atomically complete one portfolio day."""
    import uuid
    day = kwargs["day"]
    if store.get_scenario(f"portfolio-{day}") is not None:
        return {"skipped": "day already sealed", "scenario_id": f"portfolio-{day}", "day": day}
    claim_id = kwargs.pop("claim_id", None) or str(uuid.uuid4())
    claim = store.claim_portfolio_day(day, claim_id, claimed_at=datetime.now(timezone.utc))
    if claim["status"] == "completed":
        return {"skipped": "day already completed", "scenario_id": f"portfolio-{day}", "day": day}
    if claim["claim_id"] != claim_id:
        return {"skipped": "day already claimed", "scenario_id": f"portfolio-{day}", "day": day}
    try:
        result = _run_portfolio_day(store, tickers, _claim_id=claim_id, **kwargs)
        if result.get("status") == "failed_unsealed":
            store.fail_portfolio_day(day, claim_id, result["reason"])
        return result
    except TemporalRunInvalidError:
        store.fail_portfolio_day(day, claim_id, "temporal_run_invalid")
        raise
    except Exception:  # noqa: BLE001 - failure is the recovery boundary
        store.fail_portfolio_day(day, claim_id, "execution_error")
        return {"status": "failed_unsealed", "reason": "execution_error", "scenario_id": f"portfolio-{day}", "day": day}


def _run_portfolio_day(
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
    _claim_id: str,
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
    started = time.monotonic()
    if store.get_scenario(scenario_id) is not None:
        # Sealed days are immutable; a retry (manual overlap, a scheduler
        # re-fire after machine sleep) is a quiet no-op, never a crash and
        # never a second spend.
        return {"skipped": "day already sealed", "scenario_id": scenario_id, "day": day}

    planned_research_calls = len(tickers) + 1
    if planned_research_calls > MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY:
        logger.warning(
            "research call budget exceeded for portfolio day %s: planned=%d cap=%d; skipping",
            day, planned_research_calls, MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY,
        )
        return {
            "status": "failed_unsealed", "reason": "research_call_ceiling",
            "skipped": "research call budget exceeded",
            "scenario_id": scenario_id,
            "day": day,
            "research_call_budget": MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY,
            "planned_research_calls": planned_research_calls,
        }

    as_of = parse_timestamp(f"{day}T21:30:00Z")
    context = TemporalContext.at(
        TemporalMode.LIVE_CAPTURE, as_of, store=store,
        run_id=run_id or str(uuid.uuid4()),
    )
    with temporal_context(context):
        research_calls = 0
        import threading
        call_lock = threading.Lock()
        deadline_breached = False

        def budgeted_call(callable_, *args):
            nonlocal research_calls, deadline_breached
            with call_lock:
                if time.monotonic() - started >= PORTFOLIO_DAY_DEADLINE_SECONDS:
                    deadline_breached = True
                    return None
                if research_calls >= MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY:
                    return None
                research_calls += 1
            return callable_(*args)

        ratings: dict[str, str] = {}
        briefs: dict[str, str] = {}
        failures: list[str] = []
        def research_one(ticker):
            try:
                # ContextVars do not cross executor threads automatically.
                # Install the day context around each worker call so tape
                # failures invalidate the claim-owned run consistently.
                with temporal_context(context):
                    research = budgeted_call(research_fn, ticker, context)
                if research is None:
                    reason = "deadline_exceeded" if deadline_breached else "research_call_ceiling"
                    return ticker, None, None, reason
                return ticker, research["rating"], research.get("brief", ""), None
            except Exception as error:  # noqa: BLE001 - one ticker never kills the day
                return ticker, None, None, f"{ticker}: {type(error).__name__}"
        pool = ThreadPoolExecutor(max_workers=MAX_RESEARCH_WORKERS)
        futures = [pool.submit(research_one, ticker) for ticker in tickers]
        try:
            remaining = PORTFOLIO_DAY_DEADLINE_SECONDS - (time.monotonic() - started)
            for future in as_completed(futures, timeout=max(remaining, 0.1)):
                ticker, rating, brief, failure = future.result()
                if rating is not None:
                    ratings[ticker] = rating
                    briefs[ticker] = brief
                if failure:
                    failures.append(failure)
        except TimeoutError:
            # A hung provider call must not hold the day open; unfinished
            # workers are abandoned (their threads die with the process) and
            # the day fails on the deadline like any other breach.
            deadline_breached = True
            failures.append("research pool: deadline_exceeded")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        coverage = len(ratings) / len(tickers) if tickers else 0.0
        if deadline_breached:
            return {"status": "failed_unsealed", "reason": "deadline_exceeded", "skipped": "policy breach", "scenario_id": scenario_id, "day": day, "coverage": coverage, "research_call_count": research_calls, "elapsed_seconds": round(time.monotonic() - started, 3), "failures": failures}
        if coverage < MIN_TICKER_COVERAGE:
            return {"status": "failed_unsealed", "reason": "minimum_ticker_coverage", "scenario_id": scenario_id, "day": day, "coverage": coverage, "research_call_count": research_calls, "elapsed_seconds": round(time.monotonic() - started, 3), "failures": failures}

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
                "status": "failed_unsealed", "reason": "quote_coverage",
                "skipped": "all quotes missing",
                "scenario_id": scenario_id,
                "day": day,
                "coverage": coverage,
                "research_call_count": research_calls,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "failures": failures,
            }

        quoted = [ticker for ticker in ratings if ticker in quotes]
        quoted_coverage = len(quoted) / len(tickers) if tickers else 0.0
        if quoted_coverage < MIN_TICKER_COVERAGE:
            # A researched ticker without a quote is missing from the CIO
            # universe; silently shrinking that universe is not allowed.
            return {"status": "failed_unsealed", "reason": "quote_coverage", "scenario_id": scenario_id, "day": day, "coverage": quoted_coverage, "research_call_count": research_calls, "elapsed_seconds": round(time.monotonic() - started, 3), "failures": failures}

        if set(prior["positions"]) - set(quotes):
            return {"status": "failed_unsealed", "reason": "held_quote_coverage", "scenario_id": scenario_id, "day": day, "coverage": coverage, "research_call_count": research_calls, "elapsed_seconds": round(time.monotonic() - started, 3), "failures": failures}

        plan = cio_allocate(
            {t: r for t, r in ratings.items() if t in quotes},
            briefs=briefs, constraints=constraints,
            complete_llm=lambda prompt: budgeted_call(complete_llm, prompt),
        )
        if deadline_breached:
            return {"status": "failed_unsealed", "reason": "deadline_exceeded", "skipped": "policy breach", "scenario_id": scenario_id, "day": day, "coverage": coverage, "research_call_count": research_calls, "elapsed_seconds": round(time.monotonic() - started, 3), "failures": failures}
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
        final_state = {
                "cash": simulator.state.cash,
                "positions": {
                    symbol: qty
                    for symbol, qty in simulator.state.positions.items()
                    if qty
                },
                "equity": equity,
            }
        context.ensure_valid()
    summary = {
        "day": day,
        "scenario_id": scenario_id,
        "run_id": context.run_id,
        "ratings": ratings,
        "plan": plan,
        "fills": fills,
        "equity": str(equity),
        "cash": str(simulator.state.cash),
        "failures": failures,
        "coverage": len(ratings) / len(tickers) if tickers else 0.0,
        "research_call_count": research_calls,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    summary["state_evidence_id"] = store.complete_portfolio_day(
        day, _claim_id, as_of=as_of, state=final_state,
        scenario_metadata={"kind": "portfolio-day", "trade_date": day, "tickers": tickers,
                           "summary": {"coverage": summary["coverage"], "research_call_count": research_calls,
                                       "elapsed_seconds": summary["elapsed_seconds"]}},
        capture_run_id=context.run_id or "", decision=json.dumps(plan, sort_keys=True),
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
        "max_debate_rounds": 0,
        "max_risk_discuss_rounds": 0,
        "analysts_only": True,
        "checkpoint_enabled": False,
    })
    config["temporal"] = {
        **DEFAULT_CONFIG["temporal"],
        "mode": "live_capture",
        "store": str(store.root),
        "search_enabled": True,
    }
    import threading
    workers = threading.local()

    def worker_graph():
        if not hasattr(workers, "graph"):
            recorder = LangChainTapeRecorder(store)
            workers.graph = TradingAgentsGraph(config=copy.deepcopy(config), callbacks=[recorder])
        return workers.graph
    cio_recorder = LangChainTapeRecorder(store)
    cio_model = create_llm_client(
        "openai", deep_model, callbacks=[cio_recorder]
    ).get_llm()

    def research(ticker: str, context) -> dict:
        from tradingagents.temporal import TemporalContext

        worker_context = TemporalContext.at(
            context.mode,
            context.clock.as_of,
            store=context.store,
            run_id=f"{context.run_id}:research:{ticker}",
        )
        final_state, _signal = worker_graph().propagate(
            ticker, context.clock.as_of.date().isoformat(), temporal=worker_context
        )
        # A worker tape failure latches only the worker's child context; the
        # day's parent context must inherit it or a truncated tape could seal
        # (round-two re-audit, finding 8 parallel escape).
        try:
            worker_context.ensure_valid()
        except Exception:
            context.invalidate()
            raise
        reports = {
            key.removesuffix("_report"): final_state.get(key, "")
            for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report")
            if final_state.get(key)
        }
        brief = json.dumps(reports, ensure_ascii=False, sort_keys=True)[:400]
        if not brief:
            raise ValueError("empty analyst brief")
        return {"rating": "Hold", "brief": brief}

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
