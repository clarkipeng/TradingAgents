from datetime import datetime, timezone

import pandas as pd

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
