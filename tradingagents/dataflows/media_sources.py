"""Structured social/news fetchers for the media poller.

Unlike the prompt-facing fetchers in this package (which return formatted
strings for an analyst), these return lists of row dicts keyed exactly by
``media_store.COLUMNS``, each carrying the provider's stable id so repeated
polls dedup cleanly. They are the data-collection half of the poller; the
storage half is ``media_store``.

Most sources cannot be queried historically (StockTwits exposes only the latest
~30 messages; Reddit/Bluesky/X searches return only recent windows), so the only
way to obtain a historical series is to capture it as it happens.

Token-gated sources:
    truthsocial  -> TRUTHSOCIAL_TOKEN  (Mastodon session bearer; Cloudflare-gated)
    x            -> X_BEARER_TOKEN      (X/Twitter API v2; paid)
The keyless sources (stocktwits, reddit, bluesky, news) need no credentials.
"""

from __future__ import annotations

import html
import http.client
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Identified User-Agent — matches the project's prompt-facing fetchers. Reddit
# serves this on the RSS endpoint where it 403s anonymous/generic tokens.
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

_STOCKTWITS_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_REDDIT_RSS = "https://www.reddit.com/r/{sub}/search.rss?{qs}"
_DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")
_BLUESKY_SEARCH = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?{qs}"
_TRUTHSOCIAL_SEARCH = "https://truthsocial.com/api/v2/search?{qs}"
_X_SEARCH = "https://api.twitter.com/2/tweets/search/recent?{qs}"
_NEWS_RSS_FEEDS = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US",
    "https://news.google.com/rss/search?q={t}+stock&hl=en-US&gl=US&ceid=US:en",
)
# Theme/macro news uses Google News search with the theme's free-text query.
_GLOBAL_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

# Sources that run without a key. 'x' is added by the poller only when a token
# is present (see media poller's source resolution).
KEYLESS_SOURCES = ("stocktwits", "reddit", "bluesky", "truthsocial", "news")


def _iso_to_epoch(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        normalized = iso_str[:-1] + "+00:00" if iso_str.endswith("Z") else iso_str
        return datetime.fromisoformat(normalized).timestamp()
    except (ValueError, TypeError):
        return None


def _rfc822_to_epoch(date_str: str | None) -> float | None:
    """Parse an RSS 2.0 ``pubDate`` (RFC-822, e.g. 'Wed, 28 Jun 2026 12:00:00 GMT')."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).timestamp()
    except (ValueError, TypeError):
        return None


def _strip_html(text: str | None) -> str:
    """Reduce an HTML fragment (Mastodon/RSS body) to collapsed plain text."""
    if not text:
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def _get_json(url: str, headers: dict, timeout: float):
    req = Request(url, headers={"User-Agent": _UA, **headers})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        logger.warning("GET %s failed: %s", url.split("?")[0], exc)
        return None


def _row(source: str, ext_id: str, ticker: str, now: float, *,
         author=None, sentiment=None, subreddit=None,
         created_utc=None, title=None, body="") -> dict:
    return {
        "source": source, "external_id": ext_id, "ticker": ticker.upper(),
        "subreddit": subreddit, "author": author, "sentiment": sentiment,
        "created_utc": created_utc, "title": title, "body": body,
        "fetched_utc": now,
    }


def fetch_stocktwits(ticker: str, now: float, limit: int = 30,
                     timeout: float = 10.0) -> list[dict]:
    """Latest StockTwits messages as rows (dedup key: message id; carries the
    user's Bullish/Bearish label)."""
    data = _get_json(_STOCKTWITS_API.format(ticker=ticker.upper()),
                     {"Accept": "application/json"}, timeout)
    messages = data.get("messages", []) if isinstance(data, dict) else []
    rows = []
    for m in messages[:limit] if limit else messages:
        mid = m.get("id")
        if mid is None:
            continue
        sentiment_obj = (m.get("entities") or {}).get("sentiment") or {}
        rows.append(_row(
            "stocktwits", str(mid), ticker, now,
            author=(m.get("user") or {}).get("username"),
            sentiment=sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None,
            created_utc=_iso_to_epoch(m.get("created_at")),
            body=(m.get("body") or "").strip(),
        ))
    return rows


def _reddit_qs(ticker: str, limit: int) -> str:
    return urlencode({"q": ticker, "restrict_sr": "on", "sort": "new",
                      "t": "week", "limit": limit})


def fetch_reddit(ticker: str, now: float, subreddits=_DEFAULT_SUBREDDITS,
                 limit_per_sub: int = 25, timeout: float = 10.0,
                 inter_request_delay: float = 1.0) -> list[dict]:
    """Recent Reddit posts mentioning ``ticker`` (Atom search; dedup key: atom id)."""
    rows = []
    for i, sub in enumerate(subreddits):
        if i > 0:
            time.sleep(inter_request_delay)
        url = _REDDIT_RSS.format(sub=sub, qs=_reddit_qs(ticker, limit_per_sub))
        req = Request(url, headers={"User-Agent": _UA})
        try:
            with urlopen(req, timeout=timeout) as resp:
                root = ET.fromstring(resp.read())
        except HTTPError as exc:
            if exc.code == 429:
                logger.warning("Reddit 429 for r/%s · %s — backing off 5s", sub, ticker)
                time.sleep(5.0)
            else:
                logger.warning("Reddit fetch failed for r/%s · %s: %s", sub, ticker, exc)
            continue
        except (OSError, http.client.HTTPException, ET.ParseError) as exc:
            logger.warning("Reddit fetch failed for r/%s · %s: %s", sub, ticker, exc)
            continue
        for entry in root.findall("atom:entry", _ATOM_NS):
            id_el = entry.find("atom:id", _ATOM_NS)
            title_el = entry.find("atom:title", _ATOM_NS)
            published_el = entry.find("atom:published", _ATOM_NS)
            content_el = entry.find("atom:content", _ATOM_NS)
            ext_id = id_el.text if id_el is not None else None
            if not ext_id:
                continue
            rows.append(_row(
                "reddit", ext_id, ticker, now, subreddit=sub,
                created_utc=_iso_to_epoch(published_el.text if published_el is not None else None),
                title=(title_el.text if title_el is not None else "") or "",
                body=_strip_html(content_el.text if content_el is not None else ""),
            ))
    return rows


def fetch_bluesky(ticker: str, now: float, limit: int = 50,
                  timeout: float = 10.0) -> list[dict]:
    """Recent Bluesky posts via the keyless public AppView (dedup key: post uri)."""
    qs = urlencode({"q": f"${ticker}", "limit": limit, "sort": "latest"})
    data = _get_json(_BLUESKY_SEARCH.format(qs=qs), {"Accept": "application/json"}, timeout)
    posts = data.get("posts", []) if isinstance(data, dict) else []
    rows = []
    for p in posts:
        uri = p.get("uri")
        if not uri:
            continue
        record = p.get("record") or {}
        rows.append(_row(
            "bluesky", uri, ticker, now,
            author=(p.get("author") or {}).get("handle"),
            created_utc=_iso_to_epoch(record.get("createdAt")),
            body=(record.get("text") or "").strip(),
        ))
    return rows


def fetch_truthsocial(ticker: str, now: float, limit: int = 40,
                      timeout: float = 10.0) -> list[dict]:
    """Truth Social statuses (Mastodon v2 search). Needs TRUTHSOCIAL_TOKEN; degrades silently."""
    headers = {"Accept": "application/json"}
    token = os.environ.get("TRUTHSOCIAL_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    qs = urlencode({"q": ticker, "type": "statuses", "limit": limit})
    data = _get_json(_TRUTHSOCIAL_SEARCH.format(qs=qs), headers, timeout)
    statuses = data.get("statuses", []) if isinstance(data, dict) else []
    rows = []
    for s in statuses:
        sid = s.get("id")
        if sid is None:
            continue
        rows.append(_row(
            "truthsocial", str(sid), ticker, now,
            author=(s.get("account") or {}).get("username"),
            created_utc=_iso_to_epoch(s.get("created_at")),
            body=_strip_html(s.get("content")),
        ))
    return rows


def fetch_x(ticker: str, now: float, limit: int = 50,
            timeout: float = 10.0) -> list[dict]:
    """X/Twitter recent search (API v2). No-ops without X_BEARER_TOKEN."""
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return []
    qs = urlencode({
        "query": f"${ticker} lang:en -is:retweet",
        "max_results": min(max(limit, 10), 100),
        "tweet.fields": "created_at,author_id",
    })
    data = _get_json(_X_SEARCH.format(qs=qs),
                     {"Authorization": f"Bearer {token}", "Accept": "application/json"},
                     timeout)
    tweets = data.get("data", []) if isinstance(data, dict) else []
    rows = []
    for t in tweets:
        tid = t.get("id")
        if tid is None:
            continue
        rows.append(_row(
            "x", str(tid), ticker, now, author=t.get("author_id"),
            created_utc=_iso_to_epoch(t.get("created_at")),
            body=(t.get("text") or "").strip(),
        ))
    return rows


def fetch_news(ticker: str, now: float, timeout: float = 10.0) -> list[dict]:
    """Company headlines from keyless RSS 2.0 feeds (Yahoo, Google News; dedup key: guid/link)."""
    rows = []
    for feed in _NEWS_RSS_FEEDS:
        url = feed.format(t=quote(ticker.upper()))
        req = Request(url, headers={"User-Agent": _UA})
        try:
            with urlopen(req, timeout=timeout) as resp:
                root = ET.fromstring(resp.read())
        except (OSError, http.client.HTTPException, ET.ParseError, HTTPError) as exc:
            logger.warning("News RSS fetch failed (%s): %s", url.split("?")[0], exc)
            continue
        for item in root.iter("item"):
            guid_el = item.find("guid")
            link_el = item.find("link")
            title_el = item.find("title")
            date_el = item.find("pubDate")
            desc_el = item.find("description")
            ext_id = (guid_el.text if guid_el is not None else None) or \
                     (link_el.text if link_el is not None else None)
            if not ext_id:
                continue
            rows.append(_row(
                "news", ext_id, ticker, now,
                created_utc=_rfc822_to_epoch(date_el.text if date_el is not None else None),
                title=(title_el.text if title_el is not None else "") or "",
                body=_strip_html(desc_el.text if desc_el is not None else ""),
            ))
        time.sleep(0.5)
    return rows


# Registry: source name → fetcher. 'x' is keyed in but no-ops without a token.
FETCHERS = {
    "stocktwits": fetch_stocktwits,
    "reddit": fetch_reddit,
    "bluesky": fetch_bluesky,
    "truthsocial": fetch_truthsocial,
    "news": fetch_news,
    "x": fetch_x,
}
SELECTABLE_SOURCES = tuple(FETCHERS)


# --------------------------------------------------------------------------- #
# Macro snapshotting — not ticker-keyed; captured per theme once per cycle.
# These sources cannot be backfilled (Polymarket exposes only live odds; a
# global-news search at a past date is not reproducible), so the poller records
# them as they happen, exactly like the social sources.
# --------------------------------------------------------------------------- #
def fetch_global_news(query: str, now: float, theme: str,
                      timeout: float = 10.0) -> list[dict]:
    """Global/macro headlines for a free-text ``query`` (Google News RSS).

    Stored in the shared ``media_posts`` table under ``source='globalnews'`` and
    a namespaced pseudo-ticker ``@<theme>`` so the backtest loader can pull a
    theme's headline window the same way it pulls a ticker's. Dedup key: guid/link.
    """
    url = _GLOBAL_NEWS_RSS.format(q=quote(query))
    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
    except (OSError, http.client.HTTPException, ET.ParseError, HTTPError) as exc:
        logger.warning("Global news fetch failed (%s): %s", query, exc)
        return []
    rows = []
    for item in root.iter("item"):
        guid_el = item.find("guid")
        link_el = item.find("link")
        title_el = item.find("title")
        date_el = item.find("pubDate")
        desc_el = item.find("description")
        ext_id = (guid_el.text if guid_el is not None else None) or \
                 (link_el.text if link_el is not None else None)
        if not ext_id:
            continue
        rows.append(_row(
            "globalnews", ext_id, f"@{theme}", now,
            created_utc=_rfc822_to_epoch(date_el.text if date_el is not None else None),
            title=(title_el.text if title_el is not None else "") or "",
            body=_strip_html(desc_el.text if desc_el is not None else ""),
        ))
    return rows


def fetch_polymarket_odds(topic: str, now: float, theme: str,
                          limit: int = 10) -> list[dict]:
    """Live implied probabilities for ``topic`` as odds rows (keyed by theme).

    One row per open market, captured at ``now`` — repeated hourly this builds
    the probability time series the macro brief needs at any past trade date.
    Rows match ``media_store.ODDS_COLUMNS``.
    """
    from tradingagents.dataflows.polymarket import _parse_json_list, iter_forward_markets

    rows = []
    for m in iter_forward_markets(topic, limit):
        prices = _parse_json_list(m.get("outcomePrices"))
        try:
            prob = float(prices[0])
        except (ValueError, IndexError):
            continue
        market_id = str(m.get("id") or m.get("conditionId") or m.get("slug")
                        or m.get("question") or "")
        if not market_id:
            continue
        rows.append({
            "theme": theme,
            "topic": topic,
            "market_id": market_id,
            "captured_utc": now,
            "question": m.get("question"),
            "probability": prob,
            "volume": float(m.get("volumeNum") or 0),
            "resolution_utc": _iso_to_epoch(m.get("endDate")),
        })
    return rows
