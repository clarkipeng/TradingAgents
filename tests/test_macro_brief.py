"""Shared macro brief: theme resolution, brief assembly, look-ahead, fail-open.

The brief is built once per run and injected into every agent's state as
``macro_context`` (see docs/portfolio-and-macro-design.md, Part A). Fetchers are
dependency-injected so these tests run fully offline.
"""
import pytest

from tradingagents.dataflows.macro_brief import build_macro_brief, resolve_themes

# A self-contained config so tests don't depend on default_config evolving.
CONFIG = {
    "macro_brief_enabled": True,
    "macro_brief_default_themes": ["rates"],
    "global_news_lookback_days": 7,
    "global_news_article_limit": 5,
    "macro_themes": {
        "rates": {
            "queries": ["Fed rate decision"],
            "fred": ["fed_funds_rate", "10y_treasury"],
            "prediction_topics": ["Fed rate cut"],
        },
        "semiconductors": {
            "queries": ["export controls China"],
            "fred": [],
            "prediction_topics": ["chip export ban"],
        },
    },
    "ticker_themes": {"NVDA": ["semiconductors"]},
}


def _fakes():
    """Recording fetchers that echo their args, so assembly + args are testable."""
    calls = {"news": [], "macro": [], "prediction": []}

    def news_fn(curr_date, look_back, limit):
        calls["news"].append((curr_date, look_back, limit))
        return f"HEADLINES for {curr_date}"

    def macro_fn(indicator, curr_date, look_back):
        calls["macro"].append((indicator, curr_date, look_back))
        return f"FRED:{indicator}"

    def prediction_fn(topic, limit):
        calls["prediction"].append((topic, limit))
        return f"MARKET:{topic}"

    return calls, news_fn, macro_fn, prediction_fn


@pytest.mark.unit
def test_resolve_themes_unions_mapped_and_default_dedup_ordered():
    # NVDA -> its mapped themes first, then defaults, de-duped, registry-filtered.
    assert resolve_themes("NVDA", CONFIG) == ["semiconductors", "rates"]
    # Unknown ticker -> defaults only.
    assert resolve_themes("ZZZZ", CONFIG) == ["rates"]
    # Case-insensitive on the ticker.
    assert resolve_themes("nvda", CONFIG) == ["semiconductors", "rates"]


@pytest.mark.unit
def test_resolve_themes_filters_unknown_registry_entries():
    cfg = {**CONFIG, "ticker_themes": {"NVDA": ["semiconductors", "does_not_exist"]}}
    assert resolve_themes("NVDA", cfg) == ["semiconductors", "rates"]


@pytest.mark.unit
def test_brief_assembles_themes_indicators_and_markets():
    calls, news_fn, macro_fn, prediction_fn = _fakes()
    brief = build_macro_brief(
        "NVDA", "2026-01-15", CONFIG,
        news_fn=news_fn, macro_fn=macro_fn, prediction_fn=prediction_fn,
    )

    assert "Macro Brief (as of 2026-01-15)" in brief
    assert "semiconductors, rates" in brief          # active themes line
    assert "HEADLINES for 2026-01-15" in brief        # global headlines block
    assert "### Theme: semiconductors" in brief
    assert "### Theme: rates" in brief
    assert "FRED:fed_funds_rate" in brief and "FRED:10y_treasury" in brief
    assert "MARKET:chip export ban" in brief and "MARKET:Fed rate cut" in brief
    assert "export controls China" in brief           # theme query guidance


@pytest.mark.unit
def test_brief_passes_trade_date_through_no_lookahead():
    # Every fetch must use trade_date as "now" — never a later date.
    calls, news_fn, macro_fn, prediction_fn = _fakes()
    build_macro_brief(
        "NVDA", "2026-01-15", CONFIG,
        news_fn=news_fn, macro_fn=macro_fn, prediction_fn=prediction_fn,
    )
    assert calls["news"] == [("2026-01-15", 7, 5)]
    assert all(curr == "2026-01-15" for _, curr, _ in calls["macro"])


@pytest.mark.unit
def test_disabled_returns_empty_and_fetches_nothing():
    calls, news_fn, macro_fn, prediction_fn = _fakes()
    out = build_macro_brief(
        "NVDA", "2026-01-15", {**CONFIG, "macro_brief_enabled": False},
        news_fn=news_fn, macro_fn=macro_fn, prediction_fn=prediction_fn,
    )
    assert out == ""
    assert calls["news"] == [] and calls["macro"] == [] and calls["prediction"] == []


@pytest.mark.unit
def test_brief_is_fail_open_on_fetch_errors():
    # A macro-data outage degrades the brief; it must not raise into the run.
    def boom_news(*a):
        raise RuntimeError("FRED down")

    def boom_macro(*a):
        raise RuntimeError("FRED down")

    def boom_pred(*a):
        raise RuntimeError("polymarket down")

    brief = build_macro_brief(
        "NVDA", "2026-01-15", CONFIG,
        news_fn=boom_news, macro_fn=boom_macro, prediction_fn=boom_pred,
    )
    assert "<global news unavailable>" in brief
    # Failed indicator/market blocks are dropped, but the brief still builds.
    assert "### Theme: semiconductors" in brief
    assert "FRED:" not in brief
