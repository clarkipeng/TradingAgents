"""Shared macro brief — one global/thematic macro view per run.

Macro-sensitive names (semiconductors like NVDA/MU, say) trade heavily on
global and political catalysts shared across many tickers: export controls,
the Fed path, tariffs, the AI-capex cycle. Rather than have each ticker's News
Analyst rediscover that backdrop independently — N redundant FRED /
prediction-market calls and N subtly inconsistent narratives — we resolve the
relevant themes once per run, fetch their indicators and forward-looking
probabilities, and assemble a single markdown brief injected into every agent's
state as ``macro_context``.

The data fetchers are dependency-injected so the builder is unit-testable
offline; the defaults call the same vendor-routed functions the News Analyst's
tools use. Every fetch is fail-open: a macro-data outage degrades the brief to a
placeholder rather than killing the trading run.

See docs/portfolio-and-macro-design.md (Part A).
"""

from __future__ import annotations

import logging

from tradingagents.dataflows.interface import route_to_vendor

logger = logging.getLogger(__name__)


def resolve_themes(ticker: str, config: dict) -> list[str]:
    """Active macro themes for ``ticker``.

    The ticker's mapped themes (``ticker_themes``) followed by the configured
    defaults (``macro_brief_default_themes``), de-duplicated with order
    preserved, and filtered to themes that actually exist in ``macro_themes``.
    Unknown tickers resolve to the defaults only.
    """
    registry = config.get("macro_themes", {}) or {}
    mapped = list((config.get("ticker_themes", {}) or {}).get(ticker.upper(), []))
    defaults = list(config.get("macro_brief_default_themes", []) or [])
    ordered = list(dict.fromkeys(mapped + defaults))  # de-dupe, keep order
    return [t for t in ordered if t in registry]


def _default_news_fn(curr_date, look_back_days, limit):
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)


def _default_macro_fn(indicator, curr_date, look_back_days):
    return route_to_vendor("get_macro_indicators", indicator, curr_date, look_back_days)


def _default_prediction_fn(topic, limit):
    return route_to_vendor("get_prediction_markets", topic, limit)


def build_macro_brief(
    ticker: str,
    trade_date: str,
    config: dict,
    *,
    news_fn=None,
    macro_fn=None,
    prediction_fn=None,
) -> str:
    """Build the shared macro brief for ``ticker`` as of ``trade_date``.

    Returns "" when ``macro_brief_enabled`` is False (the graph then runs
    exactly as before). All fetches use ``trade_date`` as "now", so the brief
    is look-ahead-safe for backtests — historical runs see only historical
    macro data, consistent with the rest of the pipeline.

    The fetchers default to the vendor-routed implementations; tests inject
    fakes. Signatures match the News Analyst tools:
        news_fn(curr_date, look_back_days, limit) -> str
        macro_fn(indicator, curr_date, look_back_days) -> str
        prediction_fn(topic, limit) -> str
    """
    if not config.get("macro_brief_enabled", False):
        return ""

    news_fn = news_fn or _default_news_fn
    macro_fn = macro_fn or _default_macro_fn
    prediction_fn = prediction_fn or _default_prediction_fn

    themes = resolve_themes(ticker, config)
    registry = config.get("macro_themes", {}) or {}
    look_back = config.get("global_news_lookback_days", 7)
    limit = config.get("global_news_article_limit", 10)

    sections = [
        f"## Macro Brief (as of {trade_date})",
        f"Active macro themes for {ticker.upper()}: "
        + (", ".join(themes) if themes else "none"),
    ]

    # General macro headlines — fetched once for the whole run.
    sections.append("### Global Headlines\n" + _safe(
        lambda: news_fn(trade_date, look_back, limit),
        "global news", "<global news unavailable>",
    ))

    # Per-theme: indicators (FRED) + forward-looking probabilities (prediction markets).
    for theme in themes:
        spec = registry.get(theme, {})
        block = [f"### Theme: {theme}"]
        queries = spec.get("queries", [])
        if queries:
            block.append("News search terms to watch: " + "; ".join(queries))
        for indicator in spec.get("fred", []):
            out = _safe(lambda i=indicator: macro_fn(i, trade_date, None),
                        f"FRED {indicator}", None)
            if out:
                block.append(out)
        for topic in spec.get("prediction_topics", []):
            out = _safe(lambda t=topic: prediction_fn(t, None),
                        f"prediction '{topic}'", None)
            if out:
                block.append(out)
        sections.append("\n\n".join(block))

    return "\n\n".join(sections).strip()


def _safe(call, label: str, fallback):
    """Run a fetcher, returning a trimmed string or ``fallback`` on any error.

    Macro data is auxiliary context; an outage in one series must never abort
    the trading run, so we swallow and log rather than propagate.
    """
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning("macro brief: %s fetch failed: %s", label, exc)
        return fallback
    text = str(result).strip() if result is not None else ""
    return text or fallback
