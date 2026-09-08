from tradingagents.dataflows import y_finance
from tradingagents.dataflows import stockstats_utils as su
from tradingagents.dataflows.config import set_config
import pandas as pd


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


def test_ohlcv_cache_identity_preserves_basis_and_covered_window(tmp_path):
    set_config({"data_cache_dir": str(tmp_path)})
    frame = pd.DataFrame({"Date": ["2026-09-07"], "Close": [100.0]})
    full = su.cache_ohlcv(
        "AAPL", frame, start="2021-09-07", end_exclusive="2026-09-08",
    )
    partial = su.cache_ohlcv(
        "AAPL", frame, start="2026-09-01", end_exclusive="2026-09-08",
    )
    assert full != partial
    assert su.read_cached_ohlcv_window("AAPL", "2021-09-07", "2026-09-07") is not None
    assert su.read_cached_ohlcv_window("AAPL", "2026-09-01", "2026-09-07") is not None


def test_ohlcv_cache_without_basis_and_window_metadata_is_not_reused(tmp_path):
    set_config({"data_cache_dir": str(tmp_path)})
    path = su._cache_file_for("AAPL", start="2026-09-01", end_exclusive="2026-09-08")
    pd.DataFrame({"Date": ["2026-09-07"], "Close": [100.0]}).to_csv(path, index=False)
    assert su.read_cached_ohlcv_window("AAPL", "2026-09-01", "2026-09-07") is None
