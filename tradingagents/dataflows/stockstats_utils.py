import logging
import json
import os
import time
from collections import OrderedDict
from threading import Lock
from typing import Annotated

import pandas as pd
import yfinance as yf
from stockstats import wrap
from yfinance.exceptions import YFRateLimitError

from .config import get_config
from .symbol_utils import NoMarketDataError, normalize_symbol
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row this many calendar days before the requested date
# is treated as stale. Generous enough to span long holiday weekends, tight
# enough to catch the year-old frames yfinance occasionally returns (#1021).
MAX_OHLCV_STALE_DAYS = 10

# How long a same-day cache that does not yet reach the requested day may be
# reused before it is refetched (#1150). Short enough that an intraday run picks
# up today's close soon after it publishes, long enough that a day with no bar
# at all (weekend, holiday) cannot trigger a download on every call.
OHLCV_CACHE_TTL_SECONDS = 900
OHLCV_CACHE_SCHEMA_VERSION = 2
# All shared frames are raw, split/dividend-unadjusted Yahoo bars.  Consumers
# may calculate their own derived indicators, but never mix incompatible price
# bases in one cache identity.
OHLCV_CACHE_ADJUSTMENT_POLICY = "yfinance-raw-ohlcv-v1"
OHLCV_HISTORY_ADJUSTMENT_POLICY = OHLCV_CACHE_ADJUSTMENT_POLICY
PROFILE_CACHE_POLICY = "yfinance-info-v1"
PROFILE_CACHE_TTL_SECONDS = 900
_OHLCV_MEMORY_CACHE_MAX_ENTRIES = 32
_ohlcv_memory_cache: OrderedDict[str, tuple[tuple[int, int], pd.DataFrame]] = OrderedDict()
_ohlcv_memory_cache_lock = Lock()
_profile_cache: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
_profile_cache_lock = Lock()


def cached_profile(symbol: str, vintage: str, fetcher, *, policy: str = PROFILE_CACHE_POLICY) -> dict:
    """Bounded same-vintage profile reuse; the fetcher remains injectable."""
    key = (normalize_symbol(symbol), vintage, policy)
    now = time.monotonic()
    with _profile_cache_lock:
        cached = _profile_cache.get(key)
        if cached is not None and now - cached[0] <= PROFILE_CACHE_TTL_SECONDS:
            _profile_cache.move_to_end(key)
            return dict(cached[1])
    profile = dict(fetcher())
    with _profile_cache_lock:
        _profile_cache[key] = (now, dict(profile))
        _profile_cache.move_to_end(key)
        while len(_profile_cache) > _OHLCV_MEMORY_CACHE_MAX_ENTRIES:
            _profile_cache.popitem(last=False)
    return profile


def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.info(
                    "Yahoo Finance rate limited; retrying in %.0fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
            else:
                logger.warning("Yahoo Finance rate-limit retries exhausted")
                raise


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some yfinance builds leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data. Rename the first
    date-like column so indicators don't silently drop when it isn't ``Date``.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    return data


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data = _ensure_date_column(data)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def _coerce_ohlcv_dates(data: pd.DataFrame) -> pd.Series:
    """Return parsed dates from an OHLCV frame, whether Date is a column or the index."""
    if "Date" in data.columns:
        return pd.to_datetime(data["Date"], errors="coerce").dropna()
    # yfinance keeps the dates in the index (a DatetimeIndex, sometimes unnamed).
    if isinstance(data.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(data.index, errors="coerce")).dropna()
    # Fallback: expose the index and look for any date-like column.
    df = data.reset_index()
    for col in ("Date", "Datetime", "date", "index"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            if not parsed.empty:
                return parsed
    return pd.Series(dtype="datetime64[ns]")


def _assert_ohlcv_not_stale(
    data: pd.DataFrame,
    curr_date: str,
    symbol: str,
    canonical: str | None = None,
    *,
    max_stale_days: int = MAX_OHLCV_STALE_DAYS,
) -> None:
    """Reject OHLCV whose latest row is far older than curr_date.

    Raises NoMarketDataError (with a stale-specific detail) so the router treats
    it like any other "no usable data from this vendor" — try the next vendor,
    then emit one clear unavailable signal. Empty frames are left to the
    caller's existing no-data handling; this guards only the dangerous case of
    present-but-stale rows (a vendor returning a year-old frame that would
    otherwise feed wrong prices to the agent, #1021).
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(requested):
        return
    requested = requested.normalize()
    dates = _coerce_ohlcv_dates(data)
    if dates.empty:
        return
    latest = dates.max().normalize()
    stale_days = (requested - latest).days
    if stale_days > max_stale_days:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, {stale_days} days before the "
            f"requested {requested.date()} (stale) — refusing to use it",
        )


def _needs_same_day_refresh(data_file, curr_date_dt, today_date) -> bool:
    """Whether a cached frame must be refetched to reflect the requested day.

    The cache file is keyed per day, so without this a run started before the
    day's bar was final keeps serving that snapshot to every later run (#1150).
    Two distinct staleness cases exist for a current-day request: the bar may be
    missing entirely, or present but still in progress — Yahoo publishes a
    partial daily candle during market hours, whose ``Close`` is not the closing
    price. Row inspection cannot tell a partial bar from a final one, so the TTL
    governs every current-day cache. Historical requests always reuse the cache,
    since those rows are immutable.
    """
    if curr_date_dt.date() < today_date.date():
        return False
    return time.time() - os.path.getmtime(data_file) > OHLCV_CACHE_TTL_SECONDS


def _read_cached_ohlcv(data_file: str) -> pd.DataFrame:
    """Read one immutable on-disk vintage at most once per process.

    The cache key includes the exact path plus its mtime/size vintage. Callers
    receive a deep copy so indicator normalization cannot mutate a shared
    frame, and replacing a downloaded file always invalidates this layer.
    """
    stat = os.stat(data_file)
    vintage = (stat.st_mtime_ns, stat.st_size)
    with _ohlcv_memory_cache_lock:
        cached = _ohlcv_memory_cache.get(data_file)
        if cached is not None and cached[0] == vintage:
            _ohlcv_memory_cache.move_to_end(data_file)
            return cached[1].copy(deep=True)
    frame = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    with _ohlcv_memory_cache_lock:
        _ohlcv_memory_cache[data_file] = (vintage, frame.copy(deep=True))
        _ohlcv_memory_cache.move_to_end(data_file)
        while len(_ohlcv_memory_cache) > _OHLCV_MEMORY_CACHE_MAX_ENTRIES:
            _ohlcv_memory_cache.popitem(last=False)
    return frame


def _cache_file_for(
    symbol: str,
    today_date: pd.Timestamp | None = None,
    *,
    start: str | None = None,
    end_exclusive: str | None = None,
) -> str:
    """Return a cache path whose name declares its exact covered window."""
    canonical = normalize_symbol(symbol)
    safe_symbol = safe_ticker_component(canonical)
    today = today_date if today_date is not None else pd.Timestamp.today()
    start = start or (today - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    end = end_exclusive or (today + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    config = get_config()
    return os.path.join(config["data_cache_dir"], f"{safe_symbol}-YFin-data-{start}-{end}.csv")


def _cache_metadata_path(data_file: str) -> str:
    return f"{data_file}.meta.json"


def _cache_policy_matches(
    data_file: str, policy: str, *, symbol: str | None = None,
    start: str | None = None, end_exclusive: str | None = None,
) -> bool:
    metadata_path = _cache_metadata_path(data_file)
    if not os.path.isfile(metadata_path):
        return False
    try:
        with open(metadata_path, encoding="utf-8") as handle:
            metadata = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        metadata.get("schema_version") == OHLCV_CACHE_SCHEMA_VERSION
        and metadata.get("adjustment_policy") == policy
        and isinstance(metadata.get("window_start"), str)
        and isinstance(metadata.get("window_end_exclusive"), str)
        and (symbol is None or metadata.get("symbol") == normalize_symbol(symbol))
        and (start is None or metadata["window_start"] <= start)
        and (end_exclusive is None or metadata["window_end_exclusive"] >= end_exclusive)
    )


def _write_cache_metadata(data_file: str, *, symbol: str, start: str, end: str) -> None:
    payload = {
        "schema_version": OHLCV_CACHE_SCHEMA_VERSION,
        "provider": "yfinance",
        "symbol": normalize_symbol(symbol),
        "adjustment_policy": OHLCV_CACHE_ADJUSTMENT_POLICY,
        "window_start": start,
        "window_end_exclusive": end,
        "captured_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    temporary = f"{_cache_metadata_path(data_file)}.partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    os.replace(temporary, _cache_metadata_path(data_file))


def cache_ohlcv(
    symbol: str,
    data: pd.DataFrame,
    *,
    start: str,
    end_exclusive: str,
    today_date: pd.Timestamp | None = None,
    adjustment_policy: str = OHLCV_CACHE_ADJUSTMENT_POLICY,
) -> str:
    """Persist one normalized OHLCV vintage for sibling consumers."""
    today = today_date if today_date is not None else pd.Timestamp.today()
    path = _cache_file_for(symbol, today, start=start, end_exclusive=end_exclusive)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frame = _ensure_date_column(data.reset_index() if isinstance(data.index, pd.DatetimeIndex) else data.copy())
    temporary = f"{path}.partial"
    frame.to_csv(temporary, index=False, encoding="utf-8")
    os.replace(temporary, path)
    _write_cache_metadata(path, symbol=symbol, start=start, end=end_exclusive)
    metadata_path = _cache_metadata_path(path)
    with open(metadata_path, encoding="utf-8") as handle:
        metadata = json.load(handle)
    metadata["adjustment_policy"] = adjustment_policy
    temporary = f"{metadata_path}.partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, sort_keys=True)
    os.replace(temporary, metadata_path)
    with _ohlcv_memory_cache_lock:
        _ohlcv_memory_cache.pop(path, None)
    return path


def read_cached_ohlcv_window(symbol: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """Read an existing yfinance vintage without contacting a provider."""
    requested_end_exclusive = (pd.Timestamp(end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    path = _cache_file_for(symbol, start=start_date, end_exclusive=requested_end_exclusive)
    if not os.path.isfile(path):
        return None
    if not _cache_policy_matches(
        path, OHLCV_HISTORY_ADJUSTMENT_POLICY, symbol=symbol,
        start=start_date, end_exclusive=requested_end_exclusive,
    ):
        return None
    requested_end = pd.Timestamp(end_date)
    if _needs_same_day_refresh(path, requested_end, pd.Timestamp.today()):
        return None
    data = _clean_dataframe(_read_cached_ohlcv(path))
    data = data[(data["Date"] >= pd.Timestamp(start_date)) & (data["Date"] <= pd.Timestamp(end_date))]
    if data.empty:
        return None
    _assert_ohlcv_not_stale(data, end_date, symbol, normalize_symbol(symbol))
    return data


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 5 years of data up to today and caches per symbol. On
    subsequent calls the cache is reused. Rows after curr_date are
    filtered out so backtests never see future prices.
    """
    # Resolve broker/forex symbols (XAUUSD+ -> GC=F) to Yahoo's convention,
    # then reject values that would escape the cache directory when
    # interpolated into the cache filename (e.g. ``../../tmp/x``).
    canonical = normalize_symbol(symbol)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (5y to today) so one file per symbol.
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    # yfinance ``end`` is EXCLUSIVE; request tomorrow so today's row is included
    # when curr_date is the current day (#986). Look-ahead is still prevented by
    # the curr_date filter below.
    end_str = (today_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = _cache_file_for(
        canonical, today_date, start=start_str, end_exclusive=end_str,
    )

    # A cached file may be empty if a prior fetch failed (unknown symbol,
    # transient rate limit). Treat an empty/columnless cache as a miss and
    # re-fetch rather than serving the poisoned file forever.
    data = None
    if os.path.exists(data_file):
        cached = _read_cached_ohlcv(data_file)
        # Serve the cache only when it is usable and not a stale snapshot of the
        # day being requested (#1150); otherwise fall through and refetch.
        if (
            _cache_policy_matches(
                data_file, OHLCV_CACHE_ADJUSTMENT_POLICY, symbol=canonical,
                start=start_str, end_exclusive=end_str,
            )
            and not cached.empty
            and "Close" in cached.columns
            and not _needs_same_day_refresh(data_file, curr_date_dt, today_date)
        ):
            data = cached

    if data is None:
        downloaded = yf_retry(lambda: yf.download(
            canonical,
            start=start_str,
            end=end_str,
            multi_level_index=False,
            progress=False,
            auto_adjust=False,
        ))
        downloaded = _ensure_date_column(downloaded.reset_index())
        # Only cache real data — never persist an empty frame.
        if downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(
                symbol, canonical, "Yahoo Finance returned no rows"
            )
        downloaded.to_csv(data_file, index=False, encoding="utf-8")
        _write_cache_metadata(data_file, symbol=canonical, start=start_str, end=end_str)
        # The write created a new captured vintage; publish it for sibling
        # indicator calls without rereading the CSV.
        _read_cached_ohlcv(data_file)
        data = downloaded

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    # Reject a stale frame (latest row far older than curr_date) rather than
    # feeding year-old prices into indicators (#1021).
    _assert_ohlcv_not_stale(data, curr_date, symbol, canonical)

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
