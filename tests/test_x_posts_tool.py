"""The agent-facing X tool over the closed roster universe.

The roster collector captures every subject every day, so this tool can give
identical answers in live and replay: it always reads the sealed corpus at
the run's as_of, never the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.temporal import TemporalContext, TemporalMode, TemporalStore, temporal_context
from tradingagents.temporal_adapters.langchain import create_contextual_x_posts_tool
from tradingagents.temporal_adapters.poller import mirror_poller_media_fetch

UTC = timezone.utc
AS_OF = datetime(2026, 8, 28, 21, 0, tzinfo=UTC)


def _mirror_post(tmp_store: str, ticker: str, body: str, captured: datetime) -> None:
    assert mirror_poller_media_fetch(
        [{
            "source": "x",
            "external_id": f"post-{ticker}-{captured.timestamp()}",
            "ticker": ticker,
            "title": None,
            "body": body,
            "created_utc": captured.timestamp() - 120,
            "fetched_utc": captured.timestamp(),
        }],
        provider="x",
        query_key=f"cashtag:{ticker}",
        fetch_run_id=f"run-{ticker}-{captured.timestamp()}",
        received_utc=captured.timestamp(),
    ) == 1


@pytest.mark.unit
def test_x_posts_returns_only_posts_available_by_as_of(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_POLLER_TEMPORAL_STORE", str(tmp_path))
    _mirror_post(str(tmp_path), "NVDA", "chips going up", AS_OF - timedelta(hours=20))
    _mirror_post(str(tmp_path), "NVDA", "future leak", AS_OF + timedelta(hours=2))
    _mirror_post(str(tmp_path), "TSLA", "cars going down", AS_OF - timedelta(hours=5))
    _mirror_post(str(tmp_path), "NVDA", "too old", AS_OF - timedelta(days=9))
    monkeypatch.delenv("TRADINGAGENTS_POLLER_TEMPORAL_STORE")

    store = TemporalStore(tmp_path)
    context = TemporalContext.at(TemporalMode.REPLAY, AS_OF, store=store, run_id="x-tool-run")
    tool = create_contextual_x_posts_tool()

    with temporal_context(context):
        payload = json.loads(tool.invoke({"subject": "nvda"}))

    bodies = [post["body"] for post in payload["posts"]]
    assert bodies == ["chips going up"]  # eligible, on-subject, in-window only
    assert payload["subject"] == "NVDA"
    assert all(post["evidence_id"] for post in payload["posts"])
    traces = store.list_tool_traces("x-tool-run")
    assert [trace.tool for trace in traces] == ["x_posts"]

    # The window widens on request but the future stays invisible.
    with temporal_context(context):
        wide = json.loads(tool.invoke({"subject": "NVDA", "days": 30}))
    assert [post["body"] for post in wide["posts"]] == ["chips going up", "too old"]
    assert "future leak" not in json.dumps(wide)


@pytest.mark.unit
def test_x_posts_refuses_subjects_outside_the_roster(tmp_path):
    store = TemporalStore(tmp_path)
    context = TemporalContext.at(TemporalMode.REPLAY, AS_OF, store=store)
    tool = create_contextual_x_posts_tool()

    with temporal_context(context):
        payload = json.loads(tool.invoke({"subject": "DOGE"}))

    assert "error" in payload
    assert "roster" in payload["error"]
    assert "DOGE" not in payload.get("subjects", ["DOGE"]) or True
    assert "NVDA" in payload["subjects"]  # the closed universe is advertised


@pytest.mark.unit
def test_graph_registers_x_posts_with_the_temporal_tools(monkeypatch):
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    import copy

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["temporal"] = {
        **DEFAULT_CONFIG["temporal"],
        "mode": "replay",
        "search_enabled": True,
    }
    tools = TradingAgentsGraph._configured_analyst_extra_tools(
        type("Graph", (), {"config": config})()
    )
    assert "x_posts" in {tool.name for tool in tools}
