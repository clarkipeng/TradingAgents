from datetime import datetime, timezone

import pytest

from tradingagents.portfolio_replay import (
    build_portfolio_replay_manifest,
    replay_portfolio_day,
    stream_digest,
    validate_portfolio_replay_manifest,
)
from tradingagents.temporal import TemporalStore


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
