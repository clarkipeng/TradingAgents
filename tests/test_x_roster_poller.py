"""The fixed X cashtag roster: a closed query universe captured every day."""

from __future__ import annotations

import json

import pytest

from tradingagents import poller
from tradingagents.dataflows import media_sources
from tradingagents.dataflows.media_store import SqliteMediaStore
from tradingagents.dataflows.x_roster import (
    X_ROSTER_STATIC_SLOTS,
    X_ROSTER_TICKERS,
    X_ROSTER_V1_COLLECTOR_SEMANTICS_ID,
    X_ROSTER_V1_PROTOCOL_ID,
)


def _roster_row(ticker: str, captured: float) -> dict:
    return {
        "source": "x",
        "external_id": f"post-{ticker}-{int(captured)}",
        "ticker": ticker,
        "subreddit": None,
        "author": "some_user",
        "sentiment": None,
        "created_utc": captured - 60,
        "title": None,
        "body": f"Public reaction about ${ticker}",
        "fetched_utc": captured,
        "metadata": {"evidence_role": "unverified_public_reaction"},
    }


@pytest.mark.unit
def test_roster_is_fifty_unique_frozen_tickers():
    assert len(X_ROSTER_TICKERS) == 50
    assert len(set(X_ROSTER_TICKERS)) == 50
    assert all(ticker == ticker.upper() for ticker in X_ROSTER_TICKERS)
    assert X_ROSTER_STATIC_SLOTS == tuple(
        ("x", f"cashtag:{ticker}") for ticker in X_ROSTER_TICKERS
    )
    assert X_ROSTER_V1_PROTOCOL_ID.startswith("protocol_")
    assert X_ROSTER_V1_COLLECTOR_SEMANTICS_ID.startswith("collector_")


@pytest.mark.unit
def test_roster_slot_fetch_builds_the_cashtag_query_and_refuses_others(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    observed = {}

    def get_json(url, headers, timeout):
        observed["url"] = url
        return {"data": [], "meta": {}}

    monkeypatch.setattr(media_sources, "_get_json", get_json)

    assert media_sources.fetch_x_roster_slot("cashtag:NVDA", 1_800_000_000.0) == []
    assert "%24NVDA" in observed["url"]  # url-encoded $NVDA

    with pytest.raises(ValueError, match="not declared"):
        media_sources.fetch_x_roster_slot("cashtag:DOGE", 1_800_000_000.0)
    with pytest.raises(ValueError, match="cashtag"):
        media_sources.fetch_x_roster_slot("from:someone", 1_800_000_000.0)


@pytest.mark.unit
def test_roster_cycle_captures_every_slot_once_per_day(tmp_path, monkeypatch):
    now = 1_786_080_000.0
    store = SqliteMediaStore(tmp_path / "roster.db")
    monkeypatch.setattr(poller.time, "time", lambda: now)
    calls = []

    def fake_slot(query_key, captured, limit=10):
        calls.append((query_key, limit))
        ticker = query_key.removeprefix("cashtag:")
        if ticker == "AMD":
            raise media_sources.ProviderResponseError("provider hiccup")
        return [_roster_row(ticker, captured)]

    monkeypatch.setattr(poller, "fetch_x_roster_slot", fake_slot)

    slots = poller.poll_x_roster_once(store, now)

    assert slots == list(X_ROSTER_STATIC_SLOTS)
    assert len(calls) == 50
    assert all(limit == 10 for _query, limit in calls)
    receipts = [
        row for row in store.fetch_runs(limit=100)
        if row["provider"] == "x" and row["query_key"].startswith("cashtag:")
    ]
    assert len(receipts) == 50
    statuses = {row["query_key"]: row["status"] for row in receipts}
    assert statuses["cashtag:AMD"] == "failed"
    assert statuses["cashtag:NVDA"] == "success"
    assert all(
        json.loads(row["metadata_json"])["budget_category"] == "roster"
        for row in receipts
    )

    # Same day again: terminal cycle, no refetch, no new spend.
    monkeypatch.setattr(
        poller, "fetch_x_roster_slot",
        lambda *_args, **_kwargs: pytest.fail("terminal roster cycle must not refetch"),
    )
    again = poller.poll_x_roster_once(store, now + 3600)
    assert set(again) == set(X_ROSTER_STATIC_SLOTS)
    assert len(store.fetch_runs(limit=200)) == 50
    day_start = (now // 86400) * 86400  # 2026-08-07T00:00Z
    assert store.daily_cost_units("x", day_start, day_start + 86400) == 50.0
    store.close()


@pytest.mark.unit
def test_roster_posts_carry_the_ticker_label_for_point_in_time_reads(tmp_path, monkeypatch):
    now = 1_786_080_000.0
    store = SqliteMediaStore(tmp_path / "roster-labels.db")
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller, "fetch_x_roster_slot",
        lambda query_key, captured, limit=10: [
            _roster_row(query_key.removeprefix("cashtag:"), captured)
        ],
    )

    poller.poll_x_roster_once(store, now)

    rows = store.history_asof("2026-08-06", "2026-08-08", tickers=["NVDA"], sources=["x"])
    assert len(rows) == 1
    assert rows[0]["body"] == "Public reaction about $NVDA"
    store.close()
