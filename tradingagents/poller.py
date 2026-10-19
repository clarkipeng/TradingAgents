"""Media poller — accumulates social/news history for backtesting.

Polls each configured source (hourly by default), appending every new item to a
media store (local SQLite by default, or any database via ``MEDIA_DB_URL``),
deduped on the provider's stable id. See ``dataflows.media_sources`` (fetchers)
and ``dataflows.media_store`` (storage).

Designed to be cloud-hostable: every knob has an environment-variable form, so a
container can run with no CLI arguments. Env vars (CLI flags override them):

    MEDIA_POLLER_TICKERS   comma-separated, e.g. "NVDA,AAPL,TSLA"   (required)
    MEDIA_POLLER_SOURCES   subset of the sources; default = keyless (+x if token)
    MEDIA_POLLER_INTERVAL  seconds between polls in daemon mode      (default 3600)
    MEDIA_POLLER_ONCE      "1"/"true" → poll once and exit (for cron/scheduler)
    MEDIA_DB_URL           store location; default ~/.tradingagents/cache/media.db
    X_BEARER_TOKEN         enables the 'x' source (paid)
    TRUTHSOCIAL_TOKEN      enables Truth Social

Run modes:
    tradingagents-poller --tickers NVDA,AAPL          # hourly daemon
    tradingagents-poller --tickers NVDA --once        # one-shot (cron/scheduler)
    tradingagents-poller --stats                      # collection summary
    tradingagents-poller --window NVDA --end 2026-06-28 --days 7
    python -m tradingagents.poller --tickers NVDA     # equivalent
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from datetime import datetime, timezone

from tradingagents.dataflows.media_sources import (
    FETCHERS,
    KEYLESS_SOURCES,
    SELECTABLE_SOURCES,
    fetch_global_news,
    fetch_polymarket_odds,
)
from tradingagents.dataflows.media_store import open_store
from tradingagents.dataflows.trading_clock import TradingClock
from tradingagents.default_config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("media_poller")


def _env_bool(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_sources(explicit: list[str] | None) -> list[str]:
    """Sources to poll: explicit list if given, else the keyless set plus 'x'
    when X_BEARER_TOKEN is present. Validates against the registry."""
    if explicit:
        sources = explicit
    else:
        sources = list(KEYLESS_SOURCES)
        if os.environ.get("X_BEARER_TOKEN"):
            sources.append("x")
    unknown = [s for s in sources if s not in FETCHERS]
    if unknown:
        raise ValueError(f"unknown source(s): {','.join(unknown)}. "
                         f"Choose from: {','.join(SELECTABLE_SOURCES)}")
    return sources


def _within(rows: list[dict], since: float | None) -> list[dict]:
    """Keep only items posted after ``since`` (the previous poll's clock time).

    This makes each poll incremental — during continuous polling the window is
    ~1 hour, and after an overnight/weekend/holiday gap it sweeps everything
    posted since the last poll. Undated items are kept (dedup is the backstop)."""
    if since is None:
        return rows
    return [r for r in rows if r.get("created_utc") is None or r["created_utc"] > since]


def poll_once(store, tickers: list[str], sources: list[str],
              now: float, since: float | None) -> None:
    for ticker in tickers:
        parts = []
        for src in sources:
            rows = _within(FETCHERS[src](ticker, now), since)
            parts.append(f"{src} +{store.store(rows)}")
        logger.info("%s: %s", ticker, " · ".join(parts))
        time.sleep(1.0)  # be polite between tickers


def poll_macro_once(store, themes: dict, now: float, since: float | None) -> None:
    """Snapshot the macro layer: per theme, global/theme news (windowed like the
    social sources) and live Polymarket odds. Odds are always stored — each poll
    is a fresh point in the probability time series. FRED is omitted (it's fully
    historical and fetched live at backtest time)."""
    for theme, spec in themes.items():
        news_new = 0
        for query in spec.get("queries", []):
            news_new += store.store(_within(fetch_global_news(query, now, theme), since))
        odds_new = 0
        for topic in spec.get("prediction_topics", []):
            odds_new += store.store_odds(fetch_polymarket_odds(topic, now, theme))
        logger.info("macro[%s]: globalnews +%d · polymarket-odds +%d",
                    theme, news_new, odds_new)


def run_cycle(store, tickers: list[str], sources: list[str], macro_themes: dict) -> None:
    """One poll cycle over the incremental window (last_poll_utc → now)."""
    since = store.get_meta("last_poll_utc")
    now = datetime.now(timezone.utc).timestamp()
    if since:
        logger.info("Window: items posted after %s",
                    datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    poll_once(store, tickers, sources, now, since)
    if macro_themes:
        poll_macro_once(store, macro_themes, now, since)
    store.set_meta("last_poll_utc", now)


def _sleep(seconds: float, stop: dict) -> None:
    """Sleep in short slices so a stop signal is honoured promptly."""
    slept = 0.0
    while slept < seconds and not stop["flag"]:
        time.sleep(min(5.0, seconds - slept))
        slept += 5.0


def poll_forever(store, tickers: list[str], sources: list[str], interval: int,
                 macro_themes: dict, clock: TradingClock | None = None) -> None:
    stop = {"flag": False}

    def _handle(signum, _frame):
        logger.info("Received signal %s — finishing current cycle then exiting.", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    logger.info("Polling %s [%s]%s every %ds%s. Ctrl-C / SIGTERM to stop.",
                ",".join(tickers), ",".join(sources),
                " + macro" if macro_themes else "", interval,
                " during extended trading hours" if clock else "")
    while not stop["flag"]:
        if clock is not None and not clock.is_polling_time():
            wake = clock.next_open()
            wait = max(60.0, (wake - datetime.now(timezone.utc)).total_seconds())
            logger.info("Outside trading hours — sleeping until %s",
                        wake.strftime("%Y-%m-%d %H:%M UTC"))
            _sleep(wait, stop)
            continue
        run_cycle(store, tickers, sources, macro_themes)
        _sleep(interval, stop)
    logger.info("Stopped.")


def print_stats(store) -> None:
    rows = store.stats()
    if not rows:
        print("No data collected yet.")
        return
    print(f"{'TICKER':<8} {'SOURCE':<11} {'ROWS':>7}  EARLIEST → LATEST (post time, UTC)")
    for ticker, source, n, lo, hi in rows:
        lo_s = datetime.fromtimestamp(lo, timezone.utc).strftime("%Y-%m-%d %H:%M") if lo else "?"
        hi_s = datetime.fromtimestamp(hi, timezone.utc).strftime("%Y-%m-%d %H:%M") if hi else "?"
        print(f"{ticker:<8} {source:<11} {n:>7}  {lo_s} → {hi_s}")

    odds = store.odds_stats()
    if odds:
        print(f"\n{'THEME':<14} {'MARKETS':>7} {'SNAPSHOTS':>9}  EARLIEST → LATEST (capture, UTC)")
        for theme, n_markets, n_snap, lo, hi in odds:
            lo_s = datetime.fromtimestamp(lo, timezone.utc).strftime("%Y-%m-%d %H:%M") if lo else "?"
            hi_s = datetime.fromtimestamp(hi, timezone.utc).strftime("%Y-%m-%d %H:%M") if hi else "?"
            print(f"{theme:<14} {n_markets:>7} {n_snap:>9}  {lo_s} → {hi_s}")


def print_window(store, ticker: str, end: str, days: int) -> None:
    rows = store.window(ticker, end, days)
    print(f"{ticker.upper()} — {len(rows)} items in the {days}d window ending {end}:")
    for r in rows:
        ts = (datetime.fromtimestamp(r["created_utc"], timezone.utc).strftime("%Y-%m-%d %H:%M")
              if r.get("created_utc") else "?")
        tag = r.get("sentiment") or (f"r/{r['subreddit']}" if r.get("subreddit") else "")
        text = (r.get("title") or r.get("body") or "").replace("\n", " ")[:120]
        print(f"  [{ts} · {r['source']:<10} {tag:<10}] {text}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", default=os.getenv("MEDIA_POLLER_TICKERS"),
                   help="Comma-separated tickers (env: MEDIA_POLLER_TICKERS)")
    p.add_argument("--sources", default=os.getenv("MEDIA_POLLER_SOURCES"),
                   help="Comma-separated subset of: " + ",".join(SELECTABLE_SOURCES)
                        + " (env: MEDIA_POLLER_SOURCES). Default: keyless + 'x' if token set.")
    p.add_argument("--db", default=os.getenv("MEDIA_DB_URL"),
                   help="Store URL/path (env: MEDIA_DB_URL). Default: local SQLite.")
    p.add_argument("--interval", type=int,
                   default=int(os.getenv("MEDIA_POLLER_INTERVAL", "3600")),
                   help="Seconds between polls in daemon mode (env: MEDIA_POLLER_INTERVAL)")
    p.add_argument("--once", action="store_true", default=_env_bool("MEDIA_POLLER_ONCE"),
                   help="Poll once and exit (env: MEDIA_POLLER_ONCE)")
    p.add_argument("--no-macro", dest="macro", action="store_false", default=True,
                   help="Skip the macro snapshot (Polymarket odds + theme news). "
                        "Macro is on by default; it captures unrecoverable data.")
    trading_default = (os.getenv("MEDIA_POLLER_TRADING_HOURS", "true").strip().lower()
                       not in ("0", "false", "no", "off"))
    p.add_argument("--no-trading-hours", dest="trading_hours", action="store_false",
                   default=trading_default,
                   help="Poll around the clock instead of gating to market hours. "
                        "By default the daemon polls only during the extended US session "
                        "(04:00–20:00 ET) on NYSE trading days (env: MEDIA_POLLER_TRADING_HOURS).")
    p.add_argument("--stats", action="store_true", help="Print collection stats and exit")
    p.add_argument("--window", metavar="TICKER", help="Print the backtest window and exit")
    p.add_argument("--end", help="Window end date YYYY-MM-DD (default: today)")
    p.add_argument("--days", type=int, default=7, help="Window length in days (default: 7)")
    args = p.parse_args(argv)

    store = open_store(args.db)
    try:
        if args.stats:
            print_stats(store)
            return
        if args.window:
            end = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            print_window(store, args.window, end, args.days)
            return

        if not args.tickers:
            p.error("--tickers (or MEDIA_POLLER_TICKERS) is required unless using --stats/--window")
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

        explicit = ([s.strip().lower() for s in args.sources.split(",") if s.strip()]
                    if args.sources else None)
        try:
            sources = resolve_sources(explicit)
        except ValueError as exc:
            p.error(str(exc))

        if "x" in sources and not os.environ.get("X_BEARER_TOKEN"):
            logger.warning("source 'x' selected but X_BEARER_TOKEN is unset — it returns nothing.")
        if "truthsocial" in sources and not os.environ.get("TRUTHSOCIAL_TOKEN"):
            logger.warning("source 'truthsocial' selected but TRUTHSOCIAL_TOKEN is unset — "
                           "Cloudflare will likely block it.")
        macro_themes = DEFAULT_CONFIG.get("macro_themes", {}) if args.macro else {}
        logger.info("Store: %s%s", args.db or "local SQLite (default)",
                    f" · macro: {len(macro_themes)} themes" if macro_themes else " · macro off")

        if args.once:
            # One-shot (cron/manual) always polls — gating is the daemon's job;
            # schedule cron during trading hours externally if desired.
            run_cycle(store, tickers, sources, macro_themes)
        else:
            clock = TradingClock() if args.trading_hours else None
            poll_forever(store, tickers, sources, args.interval, macro_themes, clock)
    finally:
        store.close()


if __name__ == "__main__":
    main()
