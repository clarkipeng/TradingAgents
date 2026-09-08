from datetime import datetime, timezone

from tradingagents.agents.utils.news_data_tools import get_hacker_news
from tradingagents.dataflows import hacker_news
from tradingagents.temporal import (
    TemporalContext,
    TemporalMode,
    TemporalStore,
    temporal_context,
)

UTC = timezone.utc


def test_hn_tool_captures_and_replays_the_fixed_daily_feed(tmp_path, monkeypatch):
    store = TemporalStore(tmp_path)
    captured_at = datetime.now(UTC)
    rows = [
        {
            "source": "hacker_news",
            "title": "NVIDIA platform discussion",
            "metadata": {
                "evidence_role": "shadow_topic_discovery",
                "engagement": {"rank": 1, "score": 42, "comment_count": 9},
                "discussion_url": "https://news.ycombinator.com/item?id=123",
            },
        }
    ]
    monkeypatch.setattr(hacker_news, "fetch_hacker_news_stories", lambda *_args, **_kwargs: rows)
    capture = TemporalContext.at(
        TemporalMode.LIVE_CAPTURE,
        captured_at,
        store=store,
        scenario_id="hn-scenario",
    )

    with temporal_context(capture):
        captured = get_hacker_news.func()
    store.seal_scenario(
        "hn-scenario",
        as_of=datetime.now(UTC),
        basis="forward-captured",
        metadata={"ticker": "NVDA", "trade_date": captured_at.date().isoformat()},
        capture_run_id=capture.run_id,
    )

    replay = TemporalContext.from_scenario(TemporalMode.REPLAY, store, "hn-scenario")
    monkeypatch.setattr(
        hacker_news,
        "fetch_hacker_news_stories",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    with temporal_context(replay):
        replayed = get_hacker_news.func()

    assert captured == replayed
    assert "NVIDIA platform discussion" in replayed
