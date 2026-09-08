from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.config import mask_data_symbol, resolve_data_symbol
from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot
from tradingagents.temporal_adapters.tradingagents import invoke_tool


@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "the current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
) -> str:
    """Deterministic verification snapshot for exact market-data claims.

    Returns the latest OHLCV row on or before curr_date, common technical
    indicators, and recent closes. Call this before making exact claims about
    price levels, Bollinger bands, RSI, MACD, moving averages, support /
    resistance, or historical comparisons, and treat it as the source of truth.
    """
    # This is a model-visible market-data boundary, not a pure presentation
    # helper.  Routing it through invoke_tool gives capture/replay the same
    # strict request matching as every vendor tool.
    return invoke_tool(
        "market.verified_snapshot",
        {"symbol": symbol, "curr_date": curr_date, "look_back_days": look_back_days},
        lambda: _live_verified_market_snapshot(symbol, curr_date, look_back_days),
    )


def _live_verified_market_snapshot(symbol: str, curr_date: str, look_back_days: int) -> str:
    real_symbol = resolve_data_symbol(symbol)
    result = build_verified_market_snapshot(real_symbol, curr_date, look_back_days)
    return mask_data_symbol(result, symbol, real_symbol)
