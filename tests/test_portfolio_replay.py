from datetime import datetime, timezone

import pytest

from tradingagents.portfolio_replay import (
    build_portfolio_replay_manifest,
    replay_production_portfolio_day,
    replay_portfolio_day,
    stream_digest,
    validate_portfolio_replay_manifest,
)
from tradingagents.portfolio_run import run_portfolio_day
from tradingagents.temporal import (
    TemporalContext,
    TemporalGateway,
    TemporalMode,
    TemporalStore,
    current_context,
    temporal_context,
)


def test_portfolio_manifest_names_independent_worker_streams(tmp_path):
    store = TemporalStore(tmp_path)
    manifest = build_portfolio_replay_manifest(
        store, parent_run_id="day-1", tickers=["MSFT", "NVDA"]
    )

    assert [row["run_id"] for row in manifest["workers"]] == [
        "day-1:research:MSFT", "day-1:research:NVDA"
    ]
    validate_portfolio_replay_manifest(manifest)

    manifest["workers"][0]["run_id"] = "day-1"
    with pytest.raises(ValueError, match="ambiguous"):
        validate_portfolio_replay_manifest(manifest)


def test_portfolio_replay_requires_every_named_stream_to_be_consumed(tmp_path):
    store = TemporalStore(tmp_path)
    manifest = build_portfolio_replay_manifest(store, parent_run_id="day-1", tickers=["NVDA"])
    store.seal_scenario("portfolio-2026-09-06", as_of=datetime(2026, 9, 6, tzinfo=timezone.utc),
                        basis="forward-captured", metadata={"replay_manifest": manifest})
    result = replay_portfolio_day(
        store, "portfolio-2026-09-06",
        replay_worker=lambda ticker, run_id: {"ticker": ticker, "llm_call_count": 0, "tool_call_count": 0, "llm_digest": stream_digest([]), "tool_digest": stream_digest([])},
        replay_cio=lambda run_id: {"llm_call_count": 0, "tool_call_count": 0, "llm_digest": stream_digest([]), "tool_digest": stream_digest([])},
    )
    assert result["workers"][0]["ticker"] == "NVDA"
    with pytest.raises(ValueError, match="complete LLM"):
        replay_portfolio_day(
            store, "portfolio-2026-09-06",
            replay_worker=lambda *_: {"llm_call_count": 1, "tool_call_count": 0},
            replay_cio=lambda _: {"llm_call_count": 0, "tool_call_count": 0},
        )


def test_portfolio_replay_rejects_matching_counts_with_wrong_stream_digest(tmp_path):
    store = TemporalStore(tmp_path)
    manifest = build_portfolio_replay_manifest(store, parent_run_id="day-1", tickers=["NVDA"])
    store.seal_scenario(
        "portfolio-2026-09-06",
        as_of=datetime(2026, 9, 6, tzinfo=timezone.utc),
        basis="forward-captured",
        metadata={"replay_manifest": manifest},
    )
    with pytest.raises(ValueError, match="different LLM"):
        replay_portfolio_day(
            store,
            "portfolio-2026-09-06",
            replay_worker=lambda *_: {
                "llm_call_count": 0,
                "tool_call_count": 0,
                "llm_digest": "not-the-captured-stream",
                "tool_digest": stream_digest([]),
            },
            replay_cio=lambda _: {
                "llm_call_count": 0,
                "tool_call_count": 0,
                "llm_digest": stream_digest([]),
                "tool_digest": stream_digest([]),
            },
        )


def test_production_portfolio_replay_recomputes_sealed_execution(tmp_path, monkeypatch):
    store = TemporalStore(tmp_path)
    captured = run_portfolio_day(
        store, ["MSFT", "NVDA"], day="2026-09-06",
        research_fn=lambda ticker, _context: {"rating": "Hold", "brief": f"brief:{ticker}"},
        complete_llm=lambda _prompt: "{}",
        quote_fn=lambda symbol, _day: {"MSFT": "100", "NVDA": "200"}[symbol],
    )
    seen = []

    class Graph:
        def __init__(self, **_kwargs):
            pass

        def propagate(self, ticker, day, *, temporal):
            seen.append((ticker, day, temporal.source_run_id))
            return {"ticker": ticker}, "Hold"

    monkeypatch.setattr("tradingagents.graph.trading_graph.TradingAgentsGraph", Graph)
    replay = replay_production_portfolio_day(
        store,
        "portfolio-2026-09-06",
        worker_graph_factory=lambda _tape: Graph(),
        cio_entrypoint=lambda _tape: captured["plan"],
    )

    assert [item[:2] for item in seen] == [("MSFT", "2026-09-06"), ("NVDA", "2026-09-06")]
    assert [item["ticker"] for item in replay["workers"]] == ["MSFT", "NVDA"]
    assert replay["execution"]["matches_seal"] is True
    assert replay["execution"]["final_state"] == captured["final_state"]


def test_production_portfolio_replay_scopes_cio_tools_to_parent_tape(tmp_path):
    store = TemporalStore(tmp_path)
    observed = datetime(2026, 9, 6, tzinfo=timezone.utc)
    capture = TemporalContext.at(TemporalMode.LIVE_CAPTURE, observed, store=store, run_id="day-1")
    with temporal_context(capture):
        TemporalGateway(store).invoke(
            "cio.fixture", {"kind": "coverage"}, capture, lambda: {"captured": True}
        )
    manifest = build_portfolio_replay_manifest(store, parent_run_id="day-1", tickers=[])
    store.seal_scenario(
        "portfolio-2026-09-06", as_of=observed, basis="forward-captured",
        metadata={"trade_date": "2026-09-06", "replay_manifest": manifest},
    )

    def cio(_tape):
        context = current_context()
        assert context is not None
        return TemporalGateway(store).invoke(
            "cio.fixture", {"kind": "coverage"}, context,
            lambda: (_ for _ in ()).throw(AssertionError("replay contacted provider")),
        ).value

    replay = replay_production_portfolio_day(
        store, "portfolio-2026-09-06", worker_graph_factory=lambda _tape: None, cio_entrypoint=cio
    )
    assert replay["cio"]["result"] == {"captured": True}
