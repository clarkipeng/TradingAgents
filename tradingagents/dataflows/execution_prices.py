"""Raw opening prices and corporate actions, separate from analyst series."""

from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf

from .symbol_utils import normalize_symbol


def fetch_yahoo_open(symbol: str, session: str) -> dict[str, str]:
    """Fetch one explicitly unadjusted daily open; never relabel report CSV."""
    day = date.fromisoformat(session)
    frame = yf.Ticker(normalize_symbol(symbol)).history(
        start=session, end=(day + timedelta(days=1)).isoformat(),
        auto_adjust=False, back_adjust=False, actions=True, repair=False,
    )
    required = {"Open", "Stock Splits", "Dividends"}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError("raw opening price or corporate actions missing")
    eligible = frame.loc[[stamp.date() == day for stamp in frame.index]]
    if len(eligible) != 1:
        raise ValueError("raw opening session is missing or ambiguous")
    row = eligible.iloc[0]
    price, split, dividend = (Decimal(str(row[key])) for key in ("Open", "Stock Splits", "Dividends"))
    if (not all(value.is_finite() for value in (price, split, dividend))
            or price <= 0 or split < 0 or dividend < 0):
        raise ValueError("raw opening price or corporate actions invalid")
    return {
        "price": str(price), "split_ratio": str(split or Decimal("1")),
        "cash_dividend": str(dividend), "adjustment_basis": "raw-ohlcv-v1",
    }
