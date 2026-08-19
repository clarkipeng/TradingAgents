from datetime import datetime, timezone

from tradingagents.dataflows import interface, reddit, stocktwits
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
    store = TemporalStore(tmp_path)

    result = capture_daily_market_research(
        store,
        ["NVDA"],
        now=datetime(2025, 1, 2, 16, tzinfo=UTC),
    )

    assert result.attempted == 4
    assert result.completed == 3
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
