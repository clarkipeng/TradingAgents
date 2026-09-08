"""Versioned, explicit membership for a captured portfolio replay.

The manifest deliberately names independent worker streams.  Their completion
order is not replay order; each stream is consumed only by its named worker.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from tradingagents.temporal.models import canonical_json

PORTFOLIO_REPLAY_MANIFEST_VERSION = 1


def stream_digest(records: Iterable[Any]) -> str:
    """Hash stable stream facts without depending on database row ordering."""
    facts = [
        {"id": getattr(record, "call_id", getattr(record, "trace_id", "")),
         "sequence": getattr(record, "sequence", None),
         "completed": str(getattr(record, "completed_at", ""))}
        for record in records
    ]
    return hashlib.sha256(canonical_json(facts).encode("utf-8")).hexdigest()


def build_portfolio_replay_manifest(
    store: Any,
    *,
    parent_run_id: str,
    tickers: Iterable[str],
    instrument_contexts: Mapping[str, str] | None = None,
    graph_version: str = "portfolio-graph-v1",
    prompt_version: str = "portfolio-cio-v1",
    execution_policy: str = "close-legacy-v1",
) -> dict[str, Any]:
    """Describe the exact streams required by one captured portfolio day."""
    workers = []
    for ticker in sorted(set(tickers)):
        run_id = f"{parent_run_id}:research:{ticker}"
        calls = store.list_llm_calls(None, temporal_run_id=run_id)
        traces = store.list_tool_traces(run_id)
        workers.append({
            "ticker": ticker,
            "run_id": run_id,
            "llm_call_count": len(calls),
            "llm_digest": stream_digest(calls),
            "tool_call_count": len(traces),
            "tool_digest": stream_digest(traces),
            "instrument_context": (instrument_contexts or {}).get(ticker),
        })
    cio_calls = store.list_llm_calls(None, temporal_run_id=parent_run_id)
    cio_traces = store.list_tool_traces(parent_run_id)
    manifest = {
        "version": PORTFOLIO_REPLAY_MANIFEST_VERSION,
        "parent_run_id": parent_run_id,
        "workers": workers,
        "cio": {
            "run_id": parent_run_id,
            "llm_call_count": len(cio_calls),
            "llm_digest": stream_digest(cio_calls),
            "tool_call_count": len(cio_traces),
            "tool_digest": stream_digest(cio_traces),
        },
        "graph_version": graph_version,
        "prompt_version": prompt_version,
        "execution_policy": execution_policy,
    }
    manifest["digest"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    return manifest


def validate_portfolio_replay_manifest(manifest: Mapping[str, Any]) -> None:
    """Reject incomplete or ambiguous manifests before any replay starts."""
    required = {"version", "parent_run_id", "workers", "cio", "digest"}
    if set(manifest) < required or manifest.get("version") != PORTFOLIO_REPLAY_MANIFEST_VERSION:
        raise ValueError("unsupported portfolio replay manifest")
    worker_ids = [worker.get("run_id") for worker in manifest["workers"]]
    if len(worker_ids) != len(set(worker_ids)) or manifest["parent_run_id"] in worker_ids:
        raise ValueError("portfolio replay streams are ambiguous")
    unsigned = {key: value for key, value in manifest.items() if key != "digest"}
    expected = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
    if manifest["digest"] != expected:
        raise ValueError("portfolio replay manifest digest mismatch")


def replay_portfolio_day(
    store: Any,
    scenario_id: str,
    *,
    replay_worker: Any,
    replay_cio: Any,
) -> dict[str, Any]:
    """Replay every named stream through caller-owned real graph entrypoints.

    Factories stay outside this small coordinator so it cannot accidentally
    construct a live provider.  Each callback must report consumed LLM/tool
    counts; a partial tape is therefore a hard replay failure.
    """
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise KeyError(f"unknown scenario: {scenario_id}")
    manifest = scenario.metadata.get("replay_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("portfolio scenario has no replay manifest")
    validate_portfolio_replay_manifest(manifest)
    workers = []
    for stream in manifest["workers"]:
        result = replay_worker(stream["ticker"], stream["run_id"])
        _assert_consumed(stream, result)
        workers.append(result)
    cio = replay_cio(manifest["cio"]["run_id"])
    _assert_consumed(manifest["cio"], cio)
    return {"scenario_id": scenario_id, "workers": workers, "cio": cio}


def replay_production_portfolio_day(
    store: Any,
    scenario_id: str,
    *,
    worker_graph_factory: Any | None = None,
    cio_entrypoint: Any | None = None,
) -> dict[str, Any]:
    """Replay production research, allocation, execution, and sealed state.

    The default wiring constructs the real analysts-only graph with taped
    models, never a provider client. Optional factories remain a narrow test
    seam; they do not bypass CIO, accounting, or final-state verification.
    """
    from tradingagents.portfolio_run import portfolio_research_result
    from tradingagents.temporal import TemporalContext, TemporalMode, temporal_context
    from tradingagents.temporal_adapters.langchain import TapeChatModel

    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise KeyError(f"unknown scenario: {scenario_id}")
    manifest = scenario.metadata.get("replay_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("portfolio scenario has no replay manifest")
    try:
        summary = _portfolio_summary(store, manifest["parent_run_id"])
    except ValueError:
        # The explicit callback seam is retained for legacy stream assessment,
        # whose old seals predate versioned execution inputs. New/default
        # production replay never takes this branch.
        if worker_graph_factory is None or cio_entrypoint is None:
            raise
        summary = None

    default_worker_graph = worker_graph_factory is None
    if default_worker_graph:
        worker_graph_factory = _default_worker_graph_factory(
            store, scenario.metadata.get("replay_graph_config")
        )
    production_cio = cio_entrypoint is None
    regenerated = {}
    if production_cio:
        assert summary is not None
        from tradingagents.portfolio_run import cio_allocate

        def cio_entrypoint(tape):
            ratings = {ticker: value["rating"] for ticker, value in regenerated.items()}
            briefs = {ticker: value["brief"] for ticker, value in regenerated.items()}
            if canonical_json(ratings) != canonical_json(summary["ratings"]) or canonical_json(briefs) != canonical_json(summary["briefs"]):
                raise ValueError("portfolio replay research differs from sealed CIO inputs")
            return cio_allocate(
                {ticker: rating for ticker, rating in ratings.items() if ticker in summary["quotes"]},
                briefs=briefs,
                constraints=summary["constraints"],
                complete_llm=lambda prompt: tape.invoke(prompt).content,
            )

    def worker(ticker: str, source_run_id: str) -> Mapping[str, Any]:
        stream = next(item for item in manifest["workers"] if item["run_id"] == source_run_id)
        # Sentiment first tries structured output then falls back to text;
        # retain that recorded attempt rather than changing graph control flow.
        tape = TapeChatModel.from_store(
            store, source_run_id=source_run_id, structured_output_replay=True,
            match_parallel_prompts=True,
        )
        source_facts = _source_stream_facts(store, source_run_id, tape.calls)
        _assert_consumed(stream, source_facts)
        context = TemporalContext.at(
            TemporalMode.REPLAY, scenario.as_of, scenario_id=scenario_id,
            store=store, source_run_id=source_run_id,
            retrieval_generation=scenario.metadata.get("retrieval_generation"),
        )
        context.instrument_context = stream.get("instrument_context")
        with temporal_context(context):
            result, _signal = worker_graph_factory(tape).propagate(
                ticker, scenario.metadata["trade_date"], temporal=context
            )
        if tape._cursor != len(tape.calls):
            raise ValueError(
                f"portfolio replay did not consume its complete LLM stream ({tape._cursor}/{len(tape.calls)})"
            )
        if len(store.list_tool_traces(context.run_id)) != stream["tool_call_count"]:
            raise ValueError("portfolio replay did not consume its complete tool stream")
        context.ensure_valid()
        regenerated[ticker] = portfolio_research_result(result, context.instrument_context)
        return {
            "ticker": ticker, "result": result,
            **source_facts,
        }

    def cio(source_run_id: str) -> Mapping[str, Any]:
        stream = manifest["cio"]
        tape = TapeChatModel.from_store(store, source_run_id=source_run_id)
        source_facts = _source_stream_facts(store, source_run_id, tape.calls)
        _assert_consumed(stream, source_facts)
        context = TemporalContext.at(
            TemporalMode.REPLAY, scenario.as_of, scenario_id=scenario_id,
            store=store, source_run_id=source_run_id,
            retrieval_generation=scenario.metadata.get("retrieval_generation"),
        )
        # The CIO is a first-class stream, not an unscoped post-processing
        # callback.  This makes any current/future CIO tool call consume the
        # parent's ordered tape under the same no-network replay boundary.
        with temporal_context(context):
            result = cio_entrypoint(tape)
            execution = _replay_execution(store, context, summary, result, require_quotes=production_cio) if summary is not None else None
        context.ensure_valid()
        if tape._cursor != len(tape.calls):
            raise ValueError("portfolio replay did not consume its complete LLM stream")
        if len(store.list_tool_traces(context.run_id)) != stream["tool_call_count"]:
            raise ValueError("portfolio replay did not consume its complete tool stream")
        return {
            "result": result, **source_facts, "execution": execution,
        }

    replay = replay_portfolio_day(store, scenario_id, replay_worker=worker, replay_cio=cio)
    if summary is not None:
        replay["execution"] = replay["cio"]["execution"]
    return replay


def _portfolio_summary(store: Any, run_id: str) -> Mapping[str, Any]:
    record = store.get_research_run(run_id)
    if record is None or record.report_artifact_hash is None:
        raise ValueError("portfolio replay has no sealed output report")
    try:
        summary = json.loads(store.read_artifact(record.report_artifact_hash))
    except (TypeError, ValueError) as error:
        raise ValueError("portfolio replay has invalid sealed output report") from error
    required = {"ratings", "briefs", "constraints", "quotes", "prior_state", "execution", "plan", "fills", "final_state"}
    if not isinstance(summary, Mapping) or not required.issubset(summary):
        raise ValueError("portfolio replay lacks versioned execution inputs")
    return summary


def _default_worker_graph_factory(store: Any, captured_config: Any):
    """Return the production graph shape with only a supplied tape model."""
    import copy

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update({
        "llm_provider": "openai",
        "max_debate_rounds": 0,
        "max_risk_discuss_rounds": 0,
        "analysts_only": True,
        "checkpoint_enabled": False,
        "llm_max_retries": 0,
    })
    # Tool registration is graph shape. Match capture's temporal surface
    # exactly; the explicit replay context controls mode and tape selection.
    config["temporal"] = {
        **DEFAULT_CONFIG["temporal"],
        "mode": "live_capture",
        "store": str(store.root),
        "search_enabled": True,
    }
    if isinstance(captured_config, Mapping):
        allowed = {
            "llm_provider", "deep_think_llm", "quick_think_llm", "max_debate_rounds",
            "max_risk_discuss_rounds", "analysts_only", "checkpoint_enabled",
            "llm_max_retries", "llm_max_completion_tokens",
        }
        config.update({key: value for key, value in captured_config.items() if key in allowed})
        temporal = captured_config.get("temporal")
        if isinstance(temporal, Mapping):
            config["temporal"].update({
                key: value for key, value in temporal.items()
                if key in {"mode", "search_enabled", "evidence_brief", "evidence_brief_k", "hacker_news_enabled"}
            })
            config["temporal"]["store"] = str(store.root)
    return lambda tape: TradingAgentsGraph(
        config=copy.deepcopy(config), quick_thinking_llm=tape, deep_thinking_llm=tape
    )


def _replay_execution(store: Any, context: Any, summary: Mapping[str, Any], replayed_plan: Any, *, require_quotes: bool) -> dict[str, Any]:
    """Recompute the sealed simulator result and reject every divergence."""
    from datetime import datetime, timezone
    from decimal import Decimal

    from tradingagents.dataflows.interface import route_to_vendor
    from tradingagents.portfolio_run import _last_close, portfolio_close_request, rebalance_orders
    from tradingagents.temporal import TemporalGateway
    from tradingagents.temporal.simulation import MarketQuote, PortfolioSimulator

    if canonical_json(replayed_plan) != canonical_json(summary["plan"]):
        raise ValueError("portfolio replay CIO plan differs from sealed plan")
    execution = summary["execution"]
    submitted_at = datetime.fromisoformat(f"{summary['day']}T21:30:00+00:00").astimezone(timezone.utc)
    quotes = {symbol: Decimal(str(price)) for symbol, price in summary["quotes"].items()}
    # Recompute the lookup identity and CSV-to-close transformation from the
    # real input adapter. A stored derived quote alone cannot verify either.
    # The callback seam can assess legacy fixtures without a source tool tape.
    if require_quotes or store.list_tool_traces(context.source_run_id):
        for symbol in sorted(set(summary["ratings"]) | set(summary["prior_state"]["positions"])):
            request = portfolio_close_request(symbol, summary["day"])
            payload = route_to_vendor("get_stock_data", symbol, request["start"], request["end"])
            close = _last_close(payload, through=summary["day"])
            outcome = TemporalGateway(store).invoke(
                "portfolio.quote.v1", request, context,
                lambda: (_ for _ in ()).throw(AssertionError("replay contacted quote provider")),
            )
            derived = str(close) if close is not None else None
            if derived != outcome.value or close != quotes.get(symbol):
                raise ValueError("portfolio replay quote differs from sealed input")
    prior = summary["prior_state"]
    simulator = PortfolioSimulator(
        prior["cash"], fee_bps=execution["fee_bps"], slippage_bps=execution["slippage_bps"]
    )
    simulator.state.positions.update({
        symbol: Decimal(str(quantity)) for symbol, quantity in prior["positions"].items()
    })
    orders = [] if replayed_plan.get("hold_book") else rebalance_orders(
        prior, replayed_plan["weights"], quotes, submitted_at=submitted_at,
        fee_bps=execution["fee_bps"], slippage_bps=execution["slippage_bps"],
    )
    fills = []
    for order in orders:
        fill = simulator.fill_from_quote(
            order, MarketQuote(order.symbol, quotes[order.symbol], submitted_at), filled_at=submitted_at
        )
        fills.append({"symbol": fill.symbol, "side": fill.side.value,
                      "quantity": str(fill.quantity), "price": str(fill.price)})
    final_state = {
        "cash": str(simulator.state.cash),
        "positions": {symbol: str(quantity) for symbol, quantity in simulator.state.positions.items() if quantity},
        "equity": str(simulator.marked_value({symbol: quotes[symbol] for symbol in simulator.state.positions})),
    }
    if canonical_json(fills) != canonical_json(summary["fills"]):
        raise ValueError("portfolio replay fills differ from sealed result")
    if canonical_json(final_state) != canonical_json(summary["final_state"]):
        raise ValueError("portfolio replay state differs from sealed result")
    return {"orders": len(orders), "fills": fills, "final_state": final_state, "matches_seal": True}


def _source_stream_facts(store: Any, run_id: str, calls: Iterable[Any]) -> dict[str, Any]:
    calls = tuple(calls)
    traces = store.list_tool_traces(run_id)
    return {
        "llm_call_count": len(calls), "llm_digest": stream_digest(calls),
        "tool_call_count": len(traces), "tool_digest": stream_digest(traces),
    }


def _assert_consumed(stream: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    if result.get("llm_call_count") != stream["llm_call_count"]:
        raise ValueError("portfolio replay did not consume its complete LLM stream")
    if result.get("tool_call_count") != stream["tool_call_count"]:
        raise ValueError("portfolio replay did not consume its complete tool stream")
    if result.get("llm_digest") != stream["llm_digest"]:
        raise ValueError("portfolio replay consumed a different LLM stream")
    if result.get("tool_digest") != stream["tool_digest"]:
        raise ValueError("portfolio replay consumed a different tool stream")
