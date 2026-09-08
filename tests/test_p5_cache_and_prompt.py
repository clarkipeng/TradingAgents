from tradingagents.dataflows import y_finance
from tradingagents.dataflows import stockstats_utils as su


def test_fundamentals_consumer_reuses_same_vintage_profile(monkeypatch):
    su._profile_cache.clear()
    calls = []

    class FakeTicker:
        def __init__(self, symbol):
            calls.append(symbol)

        @property
        def info(self):
            calls.append("info")
            return {"longName": "Fixture Co", "sector": "Technology"}

    monkeypatch.setattr(y_finance.yf, "Ticker", FakeTicker)
    first = y_finance.get_fundamentals("AAPL", curr_date="2026-09-07")
    second = y_finance.get_fundamentals("AAPL", curr_date="2026-09-07")
    assert first == second
    assert calls == ["AAPL", "info"]
