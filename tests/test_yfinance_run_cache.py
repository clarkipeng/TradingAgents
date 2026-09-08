from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Event

import pandas as pd
import pytest

from tradingagents.dataflows import y_finance
from tradingagents.temporal import TemporalContext, TemporalMode, temporal_context


def test_exact_adjusted_history_request_reuses_only_within_one_temporal_run(monkeypatch):
    calls = []

    class Ticker:
        def __init__(self, symbol):
            calls.append(symbol)

        def history(self, **_kwargs):
            return pd.DataFrame({"Close": [1.234]}, index=pd.to_datetime(["2026-09-07"]))

    monkeypatch.setattr(y_finance.yf, "Ticker", Ticker)
    context = TemporalContext.at(TemporalMode.LIVE, datetime(2026, 9, 8, tzinfo=timezone.utc), run_id="fixture")
    with temporal_context(context):
        first = y_finance.get_YFin_data_online("AAPL", "2026-09-01", "2026-09-07")
        second = y_finance.get_YFin_data_online("AAPL", "2026-09-01", "2026-09-07")
        y_finance.get_YFin_data_online("AAPL", "2026-09-02", "2026-09-07")
    assert first == second
    assert calls == ["AAPL", "AAPL"]
    with temporal_context(TemporalContext.at(TemporalMode.LIVE, context.clock.as_of)):
        y_finance.get_YFin_data_online("AAPL", "2026-09-01", "2026-09-07")
    assert calls == ["AAPL", "AAPL", "AAPL"]


def test_concurrent_callers_share_one_fetch_and_failed_fetch_can_retry(monkeypatch):
    calls = []
    started, release = Event(), Event()
    callers = Barrier(3)

    class Ticker:
        def __init__(self, symbol):
            calls.append(symbol)

        def history(self, **_kwargs):
            started.set()
            assert release.wait(5)
            return pd.DataFrame({"Close": [1.234]}, index=pd.to_datetime(["2026-09-07"]))

    monkeypatch.setattr(y_finance.yf, "Ticker", Ticker)
    context = TemporalContext.at(TemporalMode.LIVE, datetime(2026, 9, 8, tzinfo=timezone.utc))

    def fetch():
        with temporal_context(context):
            callers.wait(timeout=5)
            return y_finance.get_YFin_data_online("AAPL", "2026-09-01", "2026-09-07")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch) for _ in range(2)]
        callers.wait(timeout=5)
        assert started.wait(5)
        release.set()
        assert futures[0].result(timeout=5) == futures[1].result(timeout=5)
    assert calls == ["AAPL"]

    monkeypatch.setattr(Ticker, "history", lambda *args, **kwargs: pd.DataFrame())
    with temporal_context(context):
        with pytest.raises(y_finance.NoMarketDataError):
            y_finance.get_YFin_data_online("NVDA", "2026-09-01", "2026-09-07")
        monkeypatch.setattr(Ticker, "history", lambda *args, **kwargs: pd.DataFrame(
            {"Close": [2.5]}, index=pd.to_datetime(["2026-09-07"])))
        assert "2.5" in y_finance.get_YFin_data_online("NVDA", "2026-09-01", "2026-09-07")
    assert calls == ["AAPL", "NVDA", "NVDA"]
