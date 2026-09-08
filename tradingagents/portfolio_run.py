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
from contextlib import contextmanager
from csv import DictReader
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, localcontext

from tradingagents.model_budget import DailyModelBudget
from tradingagents.temporal import TemporalRunInvalidError, TemporalStore
from tradingagents.temporal.clock import format_timestamp, parse_timestamp
from tradingagents.temporal.simulation import Order, OrderSide

DEFAULT_CONSTRAINTS = {
    "mode": "long-only",
    "gross_limit": 1.0,
    "max_weight": 0.10,
    "initial_cash": "100000",
}

# Ignore weight drift smaller than this fraction of equity: rebalancing to
# chase day-to-day jitter pays fees for nothing. Full exits always trade.
REBALANCE_BAND = Decimal("0.002")

# A portfolio day is deliberately bounded before any provider work starts.
# One unit is one research invocation (a full per-ticker graph run) or the
# CIO sizing call; the 30-ticker universe plus CIO plus headroom fits under it.
MAX_RESEARCH_CALLS_PER_PORTFOLIO_DAY = 40
PORTFOLIO_DAY_POLICY = {
    # Calibrated on the cloud trader machine: the GIL serializes the workers'
    # tool-side Python, so wall clock tracks single-core speed, not vCPUs,
    # and search cost scales with the corpus (240k docs at Sep 2026: a day
    # reached 28/30 tickers at 5400s - the honest budget is two hours).
    "wall_clock_seconds": 7200,
    "minimum_ticker_coverage": 0.8,
    "max_workers": 4,
    "complete_held_quote_coverage": True,
    "model_token_budget": 1_000_000,
    "model_cost_budget": "100",
    # Conservative, explicit pre-call estimate for the configured model tier.
    # Returned usage reconciliation can only refine this reservation; it must
    # never turn a request into an uncharged one.
    "model_cost_per_token": "0.0001",
    "research_max_completion_tokens": 1_200,
    "cio_max_completion_tokens": 800,
    # Headroom reserved for the final allocator before concurrent research
    # begins. These are policy ceilings, not claims about provider invoices.
    "cio_token_headroom": 20_000,
    "cio_cost_headroom": "2",
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
        # Ratings are a production constant ("Hold" for every ticker) - noise
        # that anchors the model; the universe is what matters.
        "universe": sorted(ratings),
        "briefs": briefs,
    }, ensure_ascii=False, sort_keys=True)

    def fallback(reason: str) -> dict:
        # A rejected proposal is a null signal. The only honest null action
        # for a portfolio is to hold the current book: re-deriving weights
        # from ratings liquidated everything whenever the ratings were flat.
        return {
            "source": "deterministic-fallback",
            "fallback_reason": reason,
            "hold_book": True,
            "weights": {},
            "rationale": {},
        }

    # Provider and budget failures are execution failures. Only validation of
    # a returned proposal is eligible for the documented hold-book fallback.
    returned = complete_llm(prompt)
    try:
        raw = returned.strip()
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
        rationale = proposal.get("rationale")
        plan = {
            "source": "cio-llm",
            "weights": validated,
            "rationale": rationale if isinstance(rationale, dict) else {},
        }
        gross = sum(validated.values())
        if gross > constraints["gross_limit"]:
            # Full-book proposals routinely sum a hair over the limit; the
            # constraint is enforced by construction (proportional downscale
            # can only reduce exposure), never by liquidating the book.
            scale = constraints["gross_limit"] / gross
            plan["weights"] = {t: w * scale for t, w in validated.items()}
            plan["gross_normalized_from"] = gross
        return plan
    except Exception as error:  # noqa: BLE001 - any malformed output falls back
        return fallback(f"proposal rejected ({type(error).__name__})")


def run_portfolio_day(store: TemporalStore, tickers: list[str], **kwargs) -> dict:
    """Claim, execute, and atomically complete one portfolio day."""
    import uuid
    day = kwargs["day"]
    # Durable per-day claims prevent duplicate commits.  This separate lock
    # prevents *different* dates from overlapping their predecessor reads,
    # research spend, and state writes.
    with _portfolio_chain_lock(store) as owns_chain:
        if not owns_chain:
            return {"status": "failed_unsealed", "reason": "ownership_contended", "scenario_id": f"portfolio-{day}", "day": day}
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


@contextmanager
def _portfolio_chain_lock(store: TemporalStore):
    """Acquire the process-wide portfolio owner lock without blocking work."""
    import fcntl

    lock_path = store.root / "portfolio-chain.lock"
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


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
    replay_graph_config: dict | None = None,
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
    # A backfill may compute new model output, but it must not call mutable
    # data providers and label their response historical. Its tool boundary
    # therefore reads only the evidence world eligible at the requested day.
    data_mode = (
        TemporalMode.FROZEN_EVIDENCE
        if as_of < datetime.now(timezone.utc)
        else TemporalMode.LIVE_CAPTURE
    )
    context = TemporalContext.at(
        data_mode, as_of, store=store,
        run_id=run_id or str(uuid.uuid4()),
        retrieval_generation=store.active_generation_id(),
    )
    context.model_budget = DailyModelBudget(
        store.root, day, max_tokens=PORTFOLIO_DAY_POLICY["model_token_budget"],
        max_cost=PORTFOLIO_DAY_POLICY["model_cost_budget"],
        cost_per_token=PORTFOLIO_DAY_POLICY["model_cost_per_token"],
        cio_tokens=PORTFOLIO_DAY_POLICY["cio_token_headroom"],
        cio_cost=PORTFOLIO_DAY_POLICY["cio_cost_headroom"],
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
        instrument_contexts: dict[str, str] = {}
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
                    return ticker, None, None, None, reason
                return ticker, research["rating"], research.get("brief", ""), research.get("instrument_context"), None
            except Exception as error:  # noqa: BLE001 - one ticker never kills the day
                return ticker, None, None, None, f"{ticker}: {type(error).__name__}"
        pool = ThreadPoolExecutor(max_workers=MAX_RESEARCH_WORKERS)
        futures = [pool.submit(research_one, ticker) for ticker in tickers]
        try:
            remaining = PORTFOLIO_DAY_DEADLINE_SECONDS - (time.monotonic() - started)
            for future in as_completed(futures, timeout=max(remaining, 0.1)):
                ticker, rating, brief, instrument_context, failure = future.result()
                if rating is not None:
                    ratings[ticker] = rating
                    briefs[ticker] = brief
                    if isinstance(instrument_context, str):
                        instrument_contexts[ticker] = instrument_context
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

        # A callback can fail before a child reaches its normal return path.
        # Do not turn that fatal tape/budget condition into degraded coverage.
        context.ensure_valid()

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
            try:
                quote = Decimal(str(price)) if price is not None else None
                if quote is None or not quote.is_finite() or quote <= 0:
                    raise ValueError("unusable quote")
            except (ValueError, ArithmeticError):
                failures.append(f"{symbol}: no quote")
            else:
                quotes[symbol] = quote

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
        if plan.get("hold_book"):
            orders = []
        else:
            orders = rebalance_orders(
                prior, plan["weights"], quotes, submitted_at=as_of,
                fee_bps=fee_bps, slippage_bps=slippage_bps,
            )

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
            except Exception as error:  # noqa: BLE001 - a planner/simulator disagreement is not a successful day
                return {
                    "status": "failed_unsealed", "reason": "execution_policy_error",
                    "scenario_id": scenario_id, "day": day, "coverage": coverage,
                    "research_call_count": research_calls,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "failures": failures + [f"{order.symbol}: {type(error).__name__}"],
                }
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
        # These are the exact CIO inputs, retained with the sealed output so
        # production replay can recompute rather than merely echo a plan.
        "briefs": briefs,
        "constraints": constraints,
        "quotes": {symbol: str(price) for symbol, price in quotes.items()},
        "prior_state": {
            "cash": str(prior["cash"]),
            "positions": {symbol: str(quantity) for symbol, quantity in prior["positions"].items()},
        },
        "execution": {"fee_bps": fee_bps, "slippage_bps": slippage_bps, "policy": "close-legacy-v1"},
        "plan": plan,
        "fills": fills,
        "equity": str(equity),
        "cash": str(simulator.state.cash),
        "final_state": {
            "cash": str(final_state["cash"]),
            "positions": {symbol: str(quantity) for symbol, quantity in final_state["positions"].items()},
            "equity": str(final_state["equity"]),
        },
        "failures": failures,
        "coverage": len(ratings) / len(tickers) if tickers else 0.0,
        "research_call_count": research_calls,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    # Preserve an evaluation artifact at the portfolio seam without changing
    # the current CIO prompt. Any future brief-compaction policy must replace
    # this disabled baseline only after frozen-scenario quality evidence.
    from tradingagents.analyst_brief import (
        BRIEF_FORMAT_VERSION,
        COMPACTION_POLICY_VERSION,
        frozen_comparison_artifact,
    )
    brief_baseline = json.dumps(briefs, ensure_ascii=False, sort_keys=True)
    summary["brief_policy"] = {
        "format_version": BRIEF_FORMAT_VERSION,
        "policy_version": COMPACTION_POLICY_VERSION,
        "compaction_enabled": False,
        "comparison": frozen_comparison_artifact(
            brief_baseline, brief_baseline, scenario_id=scenario_id,
        ),
    }
    from tradingagents.portfolio_replay import build_portfolio_replay_manifest
    replay_manifest = build_portfolio_replay_manifest(
        store, parent_run_id=context.run_id or "", tickers=tickers,
        instrument_contexts=instrument_contexts,
    )
    summary["state_evidence_id"] = store.complete_portfolio_day(
        day, _claim_id, as_of=as_of, state=final_state,
        scenario_metadata={"kind": "portfolio-day", "trade_date": day, "tickers": tickers,
                           "retrieval_generation": context.retrieval_generation,
                           "replay_graph_config": replay_graph_config,
                           "summary": {"coverage": summary["coverage"], "research_call_count": research_calls,
                                       "elapsed_seconds": summary["elapsed_seconds"]},
                           "replay_manifest": replay_manifest},
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
    from tradingagents.temporal import TemporalGateway, current_context
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
        # Reservation is per provider attempt, so invisible SDK retries would
        # spend outside it. Bounded completions give the budget a real ceiling.
        "llm_max_retries": 0,
        "llm_max_completion_tokens": PORTFOLIO_DAY_POLICY["research_max_completion_tokens"],
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
            recorder = LangChainTapeRecorder(store, budget_role="research")
            workers.graph = TradingAgentsGraph(config=copy.deepcopy(config), callbacks=[recorder])
        return workers.graph
    cio_recorder = LangChainTapeRecorder(store, budget_role="cio")
    cio_model = create_llm_client(
        "openai",
        deep_model,
        callbacks=[cio_recorder],
        max_retries=0,
        max_completion_tokens=PORTFOLIO_DAY_POLICY["cio_max_completion_tokens"],
    ).get_llm()

    def research(ticker: str, context) -> dict:
        from tradingagents.temporal import TemporalContext

        worker_context = TemporalContext.at(
            context.mode,
            context.clock.as_of,
            store=context.store,
            run_id=f"{context.run_id}:research:{ticker}",
            retrieval_generation=context.retrieval_generation,
        )
        worker_context.model_budget = context.model_budget
        try:
            final_state, _signal = worker_graph().propagate(
                ticker, context.clock.as_of.date().isoformat(), temporal=worker_context
            )
        except Exception:
            # A graph that aborts before its callback can mark the child
            # invalid still cannot be allowed to degrade into a sealed day.
            # The parent owns the claim and is the only context that can
            # prevent that outcome.
            context.invalidate()
            raise
        finally:
            # ``propagate`` owns the child temporal context. Its callback can
            # raise before returning, so latch the claim-owning parent in the
            # finally path rather than only after a successful graph result.
            try:
                worker_context.ensure_valid()
            except TemporalRunInvalidError:
                context.invalidate()
        reports = {
            key.removesuffix("_report"): final_state.get(key, "")
            for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report")
            if final_state.get(key)
        }
        # The CIO decides on these briefs alone; 400 chars threw away nearly
        # all of the research it just paid for. 2000 chars per ticker costs
        # about two cents a day at the deep model's input price.
        brief = json.dumps(reports, ensure_ascii=False, sort_keys=True)[:2000]
        if not brief:
            raise ValueError("empty analyst brief")
        return {
            "rating": "Hold",
            "brief": brief,
            "instrument_context": worker_context.instrument_context,
        }

    def complete(prompt: str) -> str:
        return cio_model.invoke(prompt).content

    def quote(symbol: str, day: str):
        start = format_timestamp(
            parse_timestamp(f"{day}T00:00:00Z") - timedelta(days=7)
        )[:10]
        context = current_context()
        if context is None or context.store is None:
            raise RuntimeError("portfolio quote requires a temporal context")
        request = {
            "symbol": symbol,
            "start": start,
            "end": day,
            "series": "close-v1",
        }

        def fetch_close() -> str | None:
            payload = route_to_vendor("get_stock_data", symbol, start, day)
            close = _last_close(payload, through=day)
            return str(close) if close is not None else None

        outcome = TemporalGateway(context.store).invoke(
            "portfolio.quote.v1", request, context, fetch_close, source="portfolio_quote"
        )
        return Decimal(str(outcome.value)) if outcome.value is not None else None

    replay_graph_config = {
        key: config[key]
        for key in (
            "llm_provider", "deep_think_llm", "quick_think_llm", "max_debate_rounds",
            "max_risk_discuss_rounds", "analysts_only", "checkpoint_enabled",
            "llm_max_retries", "llm_max_completion_tokens",
        )
    }
    replay_graph_config["temporal"] = {
        key: config["temporal"][key]
        for key in ("mode", "search_enabled", "evidence_brief", "evidence_brief_k", "hacker_news_enabled")
    }
    return {
        "research_fn": research,
        "complete_llm": complete,
        "quote_fn": quote,
        "replay_graph_config": replay_graph_config,
    }


def _last_close(payload, *, through: str | None = None) -> Decimal | None:
    """Return the latest eligible finite positive Close from provider CSV."""
    if not isinstance(payload, str) or payload.startswith("NO_DATA_AVAILABLE"):
        return None
    cutoff = date.fromisoformat(through) if through else None
    latest: tuple[date, Decimal] | None = None
    for row in DictReader(payload.strip().splitlines()):
        try:
            session = date.fromisoformat(str(row.get("Date", "")).strip()[:10])
            close = Decimal(str(row.get("Close", "")).strip())
        except (ArithmeticError, TypeError, ValueError):
            continue
        if not close.is_finite() or close <= 0 or (cutoff is not None and session > cutoff):
            continue
        if latest is None or session > latest[0]:
            latest = (session, close)
    return latest[1] if latest is not None else None


def rebalance_orders(
    state: dict,
    weights: dict[str, float],
    quotes: dict[str, Decimal],
    *,
    submitted_at: datetime,
    fee_bps: Decimal | str | float = 0,
    slippage_bps: Decimal | str | float = 0,
) -> list[Order]:
    """Whole-share orders moving the portfolio to its target weights.

    Sells are emitted before buys so freed cash funds the purchases; buy
    quantities floor to whole shares so the plan can never overdraw.
    Day-to-day weight jitter below the trade band is not acted on - paying
    fees to chase noise is pure waste - but a full exit (target weight zero
    on a held position) is a deliberate signal and always executes.
    """
    with localcontext() as decimal_context:
        # Local policy prevents callers' global Decimal context from changing
        # executable quantities or cash conservation.
        decimal_context.prec = 28
        decimal_context.rounding = ROUND_DOWN
        cash = Decimal(str(state["cash"]))
        positions = {symbol: Decimal(str(qty)) for symbol, qty in state["positions"].items()}
        equity = cash + sum(qty * quotes[symbol] for symbol, qty in positions.items())
        band = equity * REBALANCE_BAND
        fee_rate = Decimal(str(fee_bps)) / Decimal("10000")
        slip_rate = Decimal(str(slippage_bps)) / Decimal("10000")

        sells: list[tuple[str, Decimal]] = []
        buys: list[tuple[str, Decimal]] = []
        for symbol in sorted(set(positions) | set(weights)):
            target_value = equity * Decimal(str(weights.get(symbol, 0.0)))
            target_shares = (target_value / quotes[symbol]).to_integral_value(rounding=ROUND_DOWN)
            change = target_shares - positions.get(symbol, Decimal("0"))
            if not change:
                continue
            full_exit = weights.get(symbol, 0.0) == 0.0 and positions.get(symbol)
            if not full_exit and abs(change) * quotes[symbol] < band:
                continue
            (buys if change > 0 else sells).append((symbol, abs(change)))

        # The exact sell proceeds and buy costs use the simulator's policy.
        # Plan sells first, then allocate remaining cash in ticker order. This
        # gives deterministic residual cash rather than infeasible orders.
        for symbol, quantity in sells:
            gross = quotes[symbol] / (Decimal("1") + slip_rate) * quantity
            cash += gross * (Decimal("1") - fee_rate)
        affordable: list[tuple[str, Decimal]] = []
        for symbol, desired in buys:
            unit_cost = quotes[symbol] * (Decimal("1") + slip_rate) * (Decimal("1") + fee_rate)
            quantity = min(desired, (cash / unit_cost).to_integral_value(rounding=ROUND_DOWN))
            if quantity:
                affordable.append((symbol, quantity))
                cash -= unit_cost * quantity

        ordered = [(symbol, OrderSide.SELL, quantity) for symbol, quantity in sells]
        ordered += [(symbol, OrderSide.BUY, quantity) for symbol, quantity in affordable]
        return [Order(f"rebalance-{submitted_at:%Y%m%d}-{sequence}", symbol, side, quantity, submitted_at)
                for sequence, (symbol, side, quantity) in enumerate(ordered, start=1)]
