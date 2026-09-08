from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingagents.portfolio_execution import ExecutionQuote, execute_next_open_targets, next_open_timing


def test_next_open_execution_keeps_decision_and_fill_sessions_distinct():
    timing = next_open_timing(datetime(2026, 9, 4, 22, tzinfo=timezone.utc))
    assert timing.execution_session == "2026-09-08"  # Labor Day closure
    quote = ExecutionQuote("AAPL", timing.execution_session, Decimal("100"), timing.filled_at,
                           "raw-ohlcv-v1", "fixture-open")
    result = execute_next_open_targets(
        {"cash": "1000", "positions": {}}, {"weights": {"AAPL": 0.1}}, {"AAPL": quote}, timing=timing,
    )
    assert result["policy"] == "next-open-v1"
    assert result["fills"] and result["submitted_at"] != result["decision_ready_at"]


def test_next_open_execution_rejects_wrong_session_or_missing_evidence():
    timing = next_open_timing(datetime(2026, 9, 4, 22, tzinfo=timezone.utc))
    quote = ExecutionQuote("AAPL", "2026-09-09", Decimal("100"), timing.filled_at,
                           "raw-ohlcv-v1", "fixture-open")
    with pytest.raises(ValueError, match="different sessions"):
        execute_next_open_targets({"cash": "1000", "positions": {}}, {"weights": {"AAPL": .1}}, {"AAPL": quote}, timing=timing)
