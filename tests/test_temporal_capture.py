from datetime import datetime, timezone

from tradingagents.dataflows import hacker_news, interface, reddit, stocktwits
from tradingagents.dataflows.config import set_config
from tradingagents.temporal import TemporalStore
from tradingagents.temporal_adapters.tradingagents import capture_daily_market_research

UTC = timezone.utc


def test_daily_capture_uses_existing_router_and_continues_after_a_failure(tmp_path, monkeypatch):
    set_config(
        {
            "data_vendors": {
                "core_stock_apis": "yfinance",
                "news_data": "yfinance",
            }
        }
    )
    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "yfinance", lambda *_args: "prices")

    def unavailable_news(*_args):
        raise ValueError("news unavailable")

    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"], "yfinance", unavailable_news)
    monkeypatch.setattr(stocktwits, "fetch_stocktwits_messages", lambda *_args, **_kwargs: "stocktwits")
    monkeypatch.setattr(reddit, "fetch_reddit_posts", lambda *_args, **_kwargs: "reddit")
    monkeypatch.setattr(
        hacker_news,
        "fetch_hacker_news_stories",
        lambda *_args, **_kwargs: [
            {
                "external_id": "hn_123",
                "title": "NVIDIA news",
                "body": "HN discussion",
                "created_utc": datetime(2025, 1, 2, 15, tzinfo=UTC).timestamp(),
                "metadata": {"discussion_url": "https://news.ycombinator.com/item?id=123"},
            }
        ],
    )
    store = TemporalStore(tmp_path)

    result = capture_daily_market_research(
        store,
        ["NVDA"],
        now=datetime(2025, 1, 2, 16, tzinfo=UTC),
    )

    assert result.attempted == 5
    assert result.completed == 4
    assert result.failures == ("NVDA:get_news:ValueError",)
    assert result.start_date == "2024-12-26"
    assert result.end_date == "2025-01-02"
    assert result.run_id
    prices = store.latest_eligible(
        "dataflow.get_stock_data",
        {"args": ["NVDA", "2024-12-26", "2025-01-02"], "kwargs": {}},
        as_of=datetime.now(UTC),
    )
    news = store.latest_eligible(
        "dataflow.get_news",
        {"args": ["NVDA", "2024-12-26", "2025-01-02"], "kwargs": {}},
        as_of=datetime.now(UTC),
    )
    assert prices is not None and prices.response == "prices"
    assert prices.available_at == datetime(2025, 1, 2, 16, tzinfo=UTC)
    assert news is not None and news.is_error is True
    assert news.available_at == datetime(2025, 1, 2, 16, tzinfo=UTC)
    assert store.latest_eligible(
        "social.stocktwits", {"ticker": "NVDA", "limit": 30}, as_of=datetime.now(UTC)
    ).response == "stocktwits"
    assert store.latest_eligible(
        "social.reddit",
        {"ticker": "NVDA", "subreddits": "default", "limit_per_sub": 5},
        as_of=datetime.now(UTC),
    ).response == "reddit"
    assert store.latest_eligible(
        "social.hackernews", {"feed": "top", "limit": 8}, as_of=datetime.now(UTC)
    ).response[0]["external_id"] == "hn_123"
    document = store.search("NVIDIA", as_of=datetime(2025, 1, 2, 16, tzinfo=UTC)).results[0]
    assert document.evidence.tool == "corpus.document"
    assert document.evidence.response["metadata"]["parent_artifact_hash"]


def test_full_surface_capture_records_extended_research_tools(tmp_path, monkeypatch):
    set_config(
        {
            "data_vendors": {
                "core_stock_apis": "yfinance",
                "news_data": "yfinance",
                "fundamental_data": "yfinance",
                "macro_data": "fred",
                "prediction_markets": "polymarket",
            }
        }
    )
    for method in (
        "get_stock_data",
        "get_news",
        "get_fundamentals",
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
        "get_insider_transactions",
        "get_global_news",
    ):
        monkeypatch.setitem(
            interface.VENDOR_METHODS[method], "yfinance", lambda *_args, method=method: method
        )
    monkeypatch.setitem(interface.VENDOR_METHODS["get_macro_indicators"], "fred", lambda *_args: "macro")
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_prediction_markets"], "polymarket", lambda *_args: "markets"
    )
    monkeypatch.setattr(stocktwits, "fetch_stocktwits_messages", lambda *_args, **_kwargs: "stocktwits")
    monkeypatch.setattr(reddit, "fetch_reddit_posts", lambda *_args, **_kwargs: "reddit")
    monkeypatch.setattr(hacker_news, "fetch_hacker_news_stories", lambda *_args, **_kwargs: [])

    result = capture_daily_market_research(
        TemporalStore(tmp_path),
        ["NVDA"],
        now=datetime(2025, 1, 2, 16, tzinfo=UTC),
        full_surface=True,
    )

    assert result.attempted == result.completed == 15
    assert not result.failures


def test_capture_survives_a_long_held_external_write_transaction(tmp_path, monkeypatch):
    """Regression: a concurrent writer (e.g. the poller mirror) must not fail
    every seal with sqlite3.OperationalError, as the launchd runs did."""
    import sqlite3
    import threading
    import time

    set_config(
        {
            "data_vendors": {
                "core_stock_apis": "yfinance",
                "news_data": "yfinance",
            }
        }
    )
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_stock_data"], "yfinance", lambda *_args: "prices"
    )
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"], "yfinance", lambda *_args: "news")
    monkeypatch.setattr(stocktwits, "fetch_stocktwits_messages", lambda *_a, **_k: "stocktwits")
    monkeypatch.setattr(reddit, "fetch_reddit_posts", lambda *_a, **_k: "reddit")
    monkeypatch.setattr(hacker_news, "fetch_hacker_news_stories", lambda *_a, **_k: [])
    store = TemporalStore(tmp_path)

    def hold_then_release():
        conn = sqlite3.connect(store.database_path, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS lock_hold (x INT)")
            # Hold longer than sqlite's legacy 5s default busy timeout but
            # well under the store's configured one; a real mirror commits in
            # seconds.
            time.sleep(6.5)
        finally:
            conn.execute("ROLLBACK")
            conn.close()

    holder = threading.Thread(target=hold_then_release)
    holder.start()
    try:
        result = capture_daily_market_research(
            store,
            ["NVDA"],
            now=datetime(2025, 1, 2, 16, tzinfo=UTC),
        )
    finally:
        holder.join()

    assert result.completed == result.attempted == 5
    assert not result.failures


def test_write_lock_serializes_mutators(tmp_path):
    import fcntl

    store = TemporalStore(tmp_path)
    with store.write_lock():
        probe = open(tmp_path / "mutator.lock", "a+")
        try:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError("write_lock did not exclude a second mutator")
        finally:
            probe.close()
