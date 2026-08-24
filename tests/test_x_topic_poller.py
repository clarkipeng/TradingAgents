"""Dynamic X discovery stays broad, diverse, and tightly bounded."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from tradingagents import poller
from tradingagents.dataflows import media_sources
from tradingagents.dataflows.media_sources import _row
from tradingagents.dataflows.media_store import SqliteMediaStore
from tradingagents.dataflows.x_shadow import (
    X_SHADOW_COLLECTOR_SEMANTICS_ID,
    X_SHADOW_POLICY,
    X_SHADOW_PROTOCOL_ID,
    x_shadow_receipt_usd,
)

_X_WINDOW_OPEN_UTC = datetime(2026, 8, 5, 21, tzinfo=timezone.utc).timestamp()
_X_WINDOW_CLOSED_UTC = datetime(2026, 8, 5, 23, 46, tzinfo=timezone.utc).timestamp()
_X_TOPIC_LIMIT = int(
    poller.GLOBAL_EVENT_V2_PROTOCOL["evidence"][
        "max_x_search_requests_per_utc_day"
    ]
)


def _complete_formal_x_cycle(store, monkeypatch, now, *, topic_count=1):
    categories = ("world", "business", "technology", "general", "us")
    topics = [
        {
            "topic": f"trend_{categories[index]}",
            "category": categories[index],
            "query": f'"Global event {index}" reaction',
            "external_id": f"headline-{index}",
            "title": f"Global event {index} develops - Reuters",
            "body": "summary",
            "created_utc": now - 10 - index,
            "publisher": "Reuters",
            "metadata": {"publisher_domain": "reuters.com"},
        }
        for index in range(topic_count)
    ]
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller, "fetch_x_trends", lambda *_args, **_kwargs: [{"name": "Global event"}]
    )
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: list(topics)
    )
    monkeypatch.setattr(
        poller, "discover_x_topics", lambda max_topics, **_kwargs: list(topics)[:max_topics]
    )
    monkeypatch.setattr(poller, "fetch_x_topic", lambda *_args, **_kwargs: [])
    poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)
    cycle_id = poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)["collection_cycle_id"]
    return topics, poller._stored_x_discovery_decision(store, cycle_id)


@pytest.mark.unit
def test_x_topic_query_is_public_relevant_and_minimum_sized(monkeypatch):
    captured = {}

    def fake_get_json(url, headers, timeout):
        captured.update(url=url, headers=headers, timeout=timeout)
        return {
            "data": [{
                "id": "post-1",
                "author_id": "101",
                "created_at": "2026-07-22T12:00:00Z",
                "text": "People react to a major story",
                "public_metrics": {
                    "like_count": 2, "reply_count": 0,
                    "repost_count": 1, "quote_count": 0,
                },
            }],
            "includes": {"users": [{
                "id": "101", "username": "publicvoice", "verified_type": "none",
                "name": "Alice Example", "description": "Watching world events",
                "parody": False, "is_identity_verified": False,
                "url": "https://alice.example/profile?token=private#fragment",
                "entities": {"url": {"urls": [{
                    "expanded_url": (
                        "https://alice.example/about?api_key=private#fragment"
                    ),
                }]}},
                "created_at": "2020-01-01T00:00:00Z",
                "public_metrics": {
                    "followers_count": 100, "following_count": 20,
                    "post_count": 500,
                },
            }]},
        }

    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(media_sources, "_get_json", fake_get_json)

    rows = media_sources.fetch_x_topic(
        "trend_world", '"Bordeaux" wildfires', 1_800_000_000.0, limit=3
    )

    params = parse_qs(urlparse(captured["url"]).query)
    assert params["max_results"] == ["10"]
    assert params["sort_order"] == ["relevancy"]
    assert "Bordeaux" in params["query"][0]
    assert "-is:retweet -is:reply" in params["query"][0]
    assert "from:" not in params["query"][0]
    assert "$" not in params["query"][0]
    assert "post.fields" in params
    assert "tweet.fields" not in params
    assert {
        "description", "entities", "is_identity_verified",
        "name", "parody", "url",
    }.issubset(set(params["user.fields"][0].split(",")))
    assert "affiliation" not in params["user.fields"][0].split(",")
    assert captured["headers"]["Authorization"] == "Bearer secret-test-token"
    assert rows[0]["ticker"] == "@TREND_WORLD"
    assert rows[0]["title"] == '"Bordeaux" wildfires'
    assert rows[0]["metadata"]["evidence_role"] == "unverified_public_reaction"
    assert rows[0]["metadata"]["author_id"] == "101"
    assert rows[0]["metadata"]["automation_signals_complete"] is True
    assert rows[0]["metadata"]["profile_screening_complete"] is True
    assert rows[0]["metadata"]["organization_signals"] == []
    assert rows[0]["metadata"]["author_display_name"] == "Alice Example"
    assert rows[0]["metadata"]["author_description"] == "Watching world events"
    assert rows[0]["metadata"]["author_profile_url"] == (
        "https://alice.example/profile"
    )
    assert rows[0]["metadata"]["author_profile_entity_urls"] == [
        "https://alice.example/about"
    ]
    assert "private" not in json.dumps(rows[0]["metadata"])
    assert rows[0]["metadata"]["account_created_utc"] is not None
    assert rows[0]["metadata"]["engagement"]["retweet_count"] == 1
    assert "repost_count" not in rows[0]["metadata"]["engagement"]
    assert rows[0]["metadata"]["author_metrics"]["tweet_count"] == 500
    assert "post_count" not in rows[0]["metadata"]["author_metrics"]
    assert 0 <= rows[0]["metadata"]["automation_risk"] <= 1


@pytest.mark.unit
def test_x_topic_accepts_expanded_user_without_a_bio(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(
        media_sources,
        "_get_json",
        lambda *_args, **_kwargs: {
            "data": [{
                "id": "no-bio-post",
                "author_id": "303",
                "created_at": "2026-07-22T12:00:00Z",
                "text": "A substantive public reaction",
                "public_metrics": {
                    "like_count": 1,
                    "reply_count": 0,
                    "retweet_count": 0,
                    "quote_count": 0,
                },
            }],
            "includes": {"users": [{
                "id": "303",
                "username": "quiet_observer",
                "name": "Alice Example",
                "parody": False,
                "is_identity_verified": False,
                "verified_type": "none",
                "created_at": "2020-01-01T00:00:00Z",
                "public_metrics": {
                    "followers_count": 100,
                    "following_count": 20,
                    "tweet_count": 500,
                },
            }]},
        },
    )

    rows = media_sources.fetch_x_topic(
        "trend_world", "major event", 1_800_000_000.0
    )

    assert [row["external_id"] for row in rows] == ["no-bio-post"]
    assert rows[0]["metadata"]["author_description"] == ""
    assert rows[0]["metadata"]["profile_screening_complete"] is True


@pytest.mark.unit
def test_x_topic_excludes_an_author_with_incomplete_optional_screening(
    monkeypatch,
):
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(
        media_sources,
        "_get_json",
        lambda *_args, **_kwargs: {
            "data": [{
                "id": "unscreenable-post",
                "author_id": "404",
                "created_at": "2026-07-22T12:00:00Z",
                "text": "A substantive public reaction",
                "public_metrics": {
                    "like_count": 1,
                    "reply_count": 0,
                    "repost_count": 0,
                    "quote_count": 0,
                },
            }],
            "includes": {"users": [{
                "id": "404",
                "username": "unscreenable_user",
                "name": "Alice Example",
                "is_identity_verified": False,
                "verified_type": "none",
                "created_at": "2020-01-01T00:00:00Z",
                "public_metrics": {
                    "followers_count": 100,
                    "following_count": 20,
                    "post_count": 500,
                },
                # A wrong-typed optional flag is still schema-invalid and must
                # exclude the author; absence alone defaults to False.
                "parody": "not-a-bool",
            }]},
        },
    )

    assert media_sources.fetch_x_topic(
        "trend_world", "major event", 1_800_000_000.0
    ) == []


_ABSENT = object()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "eligible"),
    [
        # Absent or null optional assertions are tolerated: absence asserts
        # nothing, and the weakest value is assumed.
        ("parody", _ABSENT, True),
        ("parody", None, True),
        ("is_identity_verified", _ABSENT, True),
        ("is_identity_verified", None, True),
        ("description", _ABSENT, True),
        ("url", _ABSENT, True),
        ("entities", _ABSENT, True),
        ("verified_type", _ABSENT, True),
        ("verified_type", None, True),
        # Wrong-typed values are schema violations: the author is excluded.
        ("parody", "yes", False),
        ("is_identity_verified", 1, False),
        ("description", None, False),
        ("url", 42, False),
        ("entities", [], False),
        ("verified_type", "sarcastic", False),
        # Missing identity or screening prerequisites exclude the author.
        ("username", _ABSENT, False),
        ("created_at", _ABSENT, False),
        ("public_metrics", _ABSENT, False),
        ("public_metrics", {"followers_count": 100}, False),
    ],
)
def test_x_author_schema_drift_never_empties_the_batch(
    monkeypatch, field, value, eligible,
):
    """One anomalous author must cost at most that author's posts.

    X reshapes optional author fields per account (observed live: absent
    parody). Whatever the anomaly, the fetch must neither raise nor discard
    the healthy co-author's post - the failure mode that silently emptied
    every capture until the flag defaults were fixed."""
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")

    def author(author_id: str, username: str) -> dict:
        return {
            "id": author_id,
            "username": username,
            "name": "Example Person",
            "parody": False,
            "is_identity_verified": False,
            "verified_type": "none",
            "created_at": "2020-01-01T00:00:00Z",
            "public_metrics": {
                "followers_count": 100,
                "following_count": 20,
                "post_count": 500,
            },
        }

    def post(post_id: str, author_id: str) -> dict:
        return {
            "id": post_id,
            "author_id": author_id,
            "created_at": "2026-07-22T12:00:00Z",
            "text": "A substantive public reaction",
            "public_metrics": {
                "like_count": 1,
                "reply_count": 0,
                "repost_count": 0,
                "quote_count": 0,
            },
        }

    anomalous = author("501", "drifting_account")
    if value is _ABSENT:
        anomalous.pop(field, None)
    else:
        anomalous[field] = value
    monkeypatch.setattr(
        media_sources,
        "_get_json",
        lambda *_args, **_kwargs: {
            "data": [post("drift-post", "501"), post("healthy-post", "502")],
            "includes": {"users": [anomalous, author("502", "steady_account")]},
        },
    )

    rows = media_sources.fetch_x_topic("trend_world", "major event", 1_800_000_000.0)

    surviving = [row["external_id"] for row in rows]
    assert "healthy-post" in surviving
    assert ("drift-post" in surviving) == eligible


@pytest.mark.unit
def test_x_topic_defaults_absent_optional_flags_to_false(monkeypatch):
    """X omits the parody/is_identity_verified flags for some accounts;
    absence is treated as not asserted rather than discarding the author."""
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(
        media_sources,
        "_get_json",
        lambda *_args, **_kwargs: {
            "data": [{
                "id": "absent-flag-post",
                "author_id": "405",
                "created_at": "2026-07-22T12:00:00Z",
                "text": "A public reaction without optional flags",
                "public_metrics": {
                    "like_count": 1,
                    "reply_count": 0,
                    "repost_count": 0,
                    "quote_count": 0,
                },
            }],
            "includes": {"users": [{
                "id": "405",
                "username": "flagless_user",
                "name": "Flagless Example",
                "verified_type": "blue",
                "created_at": "2020-01-01T00:00:00Z",
                "public_metrics": {
                    "followers_count": 100,
                    "following_count": 20,
                    "post_count": 500,
                },
            }]},
        },
    )

    rows = media_sources.fetch_x_topic("trend_world", "major event", 1_800_000_000.0)
    assert len(rows) == 1
    assert rows[0]["metadata"]["author_parody"] is False


@pytest.mark.unit
def test_x_metric_aliases_must_be_unambiguous():
    aliases = media_sources.GLOBAL_X_ADAPTER_POLICY["recent_search"][
        "response_metric_aliases"
    ]["post"]

    with pytest.raises(media_sources.ProviderResponseError, match="metrics schema"):
        media_sources._normalize_x_metrics(
            {
                "like_count": 1,
                "reply_count": 0,
                "retweet_count": 2,
                "repost_count": 2,
                "quote_count": 0,
            },
            aliases,
        )


@pytest.mark.unit
def test_x_topic_excludes_official_business_accounts(monkeypatch):
    excluded_type = media_sources.GLOBAL_X_ADAPTER_POLICY["recent_search"][
        "excluded_verified_types"
    ][0]

    def fake_get_json(url, headers, timeout):
        return {
            "data": [
                {
                    "id": "company-post", "author_id": "1", "text": "Announcement",
                    "created_at": "2026-07-22T12:00:00Z",
                    "public_metrics": {
                        "like_count": 1, "reply_count": 0,
                        "retweet_count": 0, "quote_count": 0,
                    },
                },
                {
                    "id": "person-post", "author_id": "2", "text": "My reaction",
                    "created_at": "2026-07-22T12:00:00Z",
                    "public_metrics": {
                        "like_count": 1, "reply_count": 0,
                        "retweet_count": 0, "quote_count": 0,
                    },
                },
                {
                    "id": "unverified-company-post", "author_id": "3",
                    "text": "Our product announcement",
                    "created_at": "2026-07-22T12:00:00Z",
                    "public_metrics": {
                        "like_count": 1, "reply_count": 0,
                        "retweet_count": 0, "quote_count": 0,
                    },
                },
            ],
            "includes": {"users": [
                {
                    "id": "1", "username": "officialco",
                    "verified_type": excluded_type,
                    "name": "Acme Products", "description": "",
                    "parody": False, "is_identity_verified": True,
                    "created_at": "2020-01-01T00:00:00Z",
                    "public_metrics": {
                        "followers_count": 100, "following_count": 20,
                        "tweet_count": 500,
                    },
                },
                {
                    "id": "2", "username": "publicvoice", "verified_type": "none",
                    "name": "Alice Example", "description": "Watching world events",
                    "parody": False, "is_identity_verified": False,
                    "created_at": "2020-01-01T00:00:00Z",
                    "public_metrics": {
                        "followers_count": 100, "following_count": 20,
                        "tweet_count": 500,
                    },
                },
                {
                    "id": "3", "username": "acme_updates", "verified_type": "none",
                    "name": "Acme", "description": "Official company account",
                    "parody": False, "is_identity_verified": False,
                    "created_at": "2020-01-01T00:00:00Z",
                    "public_metrics": {
                        "followers_count": 100, "following_count": 20,
                        "tweet_count": 500,
                    },
                },
            ]},
        }

    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(media_sources, "_get_json", fake_get_json)

    rows = media_sources.fetch_x_topic("trend_world", "major event", 123.0)

    assert [row["external_id"] for row in rows] == ["person-post"]
    assert rows[0]["author"] == "publicvoice"


@pytest.mark.unit
def test_x_ineligible_author_does_not_discard_valid_sibling(monkeypatch):
    def fake_get_json(url, headers, timeout):
        return {
            "data": [
                {
                    "id": "ineligible-post", "author_id": "202",
                    "text": "A substantive public reaction",
                    "created_at": "2026-07-22T12:00:00Z",
                    "public_metrics": {
                        "like_count": 1, "reply_count": 0,
                        "retweet_count": 0, "quote_count": 0,
                    },
                },
                {
                    "id": "valid-post", "author_id": "203",
                    "text": "Another substantive public reaction",
                    "created_at": "2026-07-22T12:00:00Z",
                    "public_metrics": {
                        "like_count": 1, "reply_count": 0,
                        "retweet_count": 0, "quote_count": 0,
                    },
                },
            ],
            "includes": {"users": [
                {
                    "id": "202", "username": "incomplete_user",
                    "verified_type": "none",
                    "name": "Alice Example",
                    "description": "Watching world events",
                    "parody": False, "is_identity_verified": False,
                    "public_metrics": {
                        "followers_count": 100, "following_count": 20,
                        "tweet_count": 500,
                    },
                },
                {
                    "id": "203", "username": "public_user",
                    "verified_type": "none",
                    "name": "Bob Example",
                    "description": "Watching world events",
                    "parody": False, "is_identity_verified": False,
                    "created_at": "2020-01-01T00:00:00Z",
                    "public_metrics": {
                        "followers_count": 100, "following_count": 20,
                        "tweet_count": 500,
                    },
                },
            ]},
        }

    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(media_sources, "_get_json", fake_get_json)

    rows = media_sources.fetch_x_topic(
        "trend_world", "major event", 1_800_000_000.0
    )

    assert [row["external_id"] for row in rows] == ["valid-post"]


@pytest.mark.unit
@pytest.mark.parametrize("verified_type", ["unknown-tier", 7])
def test_x_unknown_verified_type_excludes_author(
    monkeypatch, verified_type,
):
    # An absent verified_type defaults to "none" (same drift class as the
    # absent parody flag); unknown or wrong-typed values still exclude.
    user = {
        "id": "202",
        "username": "public_user",
        "name": "Alice Example",
        "description": "Watching world events",
        "parody": False,
        "is_identity_verified": False,
        "created_at": "2020-01-01T00:00:00Z",
        "public_metrics": {
            "followers_count": 100,
            "following_count": 20,
            "tweet_count": 500,
        },
        "verified_type": verified_type,
    }
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(
        media_sources,
        "_get_json",
        lambda *_args, **_kwargs: {
            "data": [{
                "id": "post",
                "author_id": "202",
                "text": "A substantive public reaction",
                "created_at": "2026-07-22T12:00:00Z",
                "public_metrics": {
                    "like_count": 1,
                    "reply_count": 0,
                    "retweet_count": 0,
                    "quote_count": 0,
                },
            }],
            "includes": {"users": [user]},
        },
    )

    assert media_sources.fetch_x_topic(
        "trend_world", "major event", 1_800_000_000.0
    ) == []


@pytest.mark.unit
def test_x_profile_screen_is_complete_and_conservative():
    policy = media_sources.GLOBAL_X_ADAPTER_POLICY["recent_search"]
    person = {
        "username": "alice_example",
        "name": "Alice Example",
        "description": "Watching world events",
        "parody": False,
        "is_identity_verified": False,
    }
    assert media_sources._x_author_profile(person, policy)[
        "organization_signals"
    ] == []

    # Absent optional flags default to False (X omits them for some accounts).
    incomplete = dict(person)
    incomplete.pop("parody")
    assert media_sources._x_author_profile(incomplete, policy)[
        "organization_signals"
    ] == []

    wrong_typed = {**person, "parody": "yes"}
    with pytest.raises(
        media_sources.ProviderResponseError, match="author profile"
    ):
        media_sources._x_author_profile(wrong_typed, policy)

    invalid_description = {**person, "description": None}
    with pytest.raises(
        media_sources.ProviderResponseError, match="author profile"
    ):
        media_sources._x_author_profile(invalid_description, policy)

    organization = {**person, "username": "acme_corp"}
    assert media_sources._x_author_profile(organization, policy)[
        "organization_signals"
    ] == ["username_organization_language"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "profile_fields",
    [
        {"url": "javascript:alert(1)"},
        {"entities": {"url": {"urls": [{"expanded_url": {"bad": "url"}}]}}},
    ],
)
def test_x_author_profile_rejects_malformed_present_urls(profile_fields):
    policy = media_sources.GLOBAL_X_ADAPTER_POLICY["recent_search"]
    person = {
        "username": "alice_example",
        "name": "Alice Example",
        "description": "Watching world events",
        "parody": False,
        "is_identity_verified": False,
        **profile_fields,
    }

    with pytest.raises(
        media_sources.ProviderResponseError, match="profile URL"
    ):
        media_sources._x_author_profile(person, policy)


@pytest.mark.unit
def test_discovery_allocates_two_technology_one_us_and_two_global_stories(
    monkeypatch,
):
    headlines = [
        {
            "external_id": "global",
            "title": "US administration announces global tariff policy - Reuters",
            "body": "",
            "created_utc": 10.0,
            "publisher": "Reuters",
            "category": "world",
            "region": "US",
            "rank": 0,
        },
        {
            "external_id": "us",
            "title": "US Congress passes a national immigration policy - AP",
            "body": "",
            "created_utc": 10.5,
            "publisher": "Associated Press",
            "category": "us",
            "region": "US",
            "rank": 1,
        },
        {
            "external_id": "chips",
            "title": "China chip export controls disrupt semiconductor supply - BBC",
            "body": "",
            "created_utc": 11.0,
            "publisher": "BBC",
            "category": "world",
            "region": "GB",
            "rank": 2,
        },
        {
            "external_id": "rates",
            "title": "European Central Bank cuts interest rates - Reuters",
            "body": "",
            "created_utc": 12.5,
            "publisher": "Reuters",
            "category": "business",
            "region": "GB",
            "rank": 1,
        },
        {
            "external_id": "memory",
            "title": "South Korea expands HBM production capacity - Reuters",
            "body": "",
            "created_utc": 12.0,
            "publisher": "Reuters",
            "category": "business",
            "region": "US",
            "rank": 3,
        },
        {
            "external_id": "phone",
            "title": "New foldable phone launches with AI features - TechCrunch",
            "body": "",
            "created_utc": 13.0,
            "publisher": "TechCrunch",
            "category": "technology",
            "region": "US",
            "rank": 0,
        },
        {
            "external_id": "company",
            "title": "Company launches frontier AI model - Company Newsroom",
            "body": "",
            "created_utc": 14.0,
            "publisher": "Company Newsroom",
            "category": "technology",
            "region": "US",
            "rank": 0,
        },
    ]
    feed_limits = []

    def fetch_headlines(*, limit_per_feed):
        feed_limits.append(limit_per_feed)
        return headlines

    monkeypatch.setattr(poller, "fetch_top_news_headlines", fetch_headlines)
    monkeypatch.setattr(
        poller, "fetch_x_trends",
        lambda woeid, **_kwargs: (
            [{"name": "China chip export", "tweet_count": 1000}]
            if woeid == 1 else []
        ),
    )

    topics = poller.discover_x_topics(max_topics=5)

    assert [topic["topic"] for topic in topics] == [
        "trend_slot_1", "trend_slot_2", "trend_slot_3", "trend_slot_4",
        "trend_slot_5",
    ]
    assert [topic["selection_role"] for topic in topics] == [
        "strategic_technology", "strategic_technology", "major_us",
        "major_global", "major_global",
    ]
    assert all(topic["query"] for topic in topics)
    assert {topic["external_id"] for topic in topics} == {
        "global", "us", "chips", "memory", "rates",
    }
    assert all(topic["strategic_technology"] for topic in topics[:2])
    assert all(not topic["strategic_technology"] for topic in topics[2:])
    assert topics[2]["us_ranked_story"] is True
    assert feed_limits == [20]


@pytest.mark.unit
def test_us_slot_uses_national_feed_not_a_us_locale_world_feed():
    def headline(external_id, title, category, region, rank):
        return {
            "external_id": external_id,
            "title": f"{title} - Reuters",
            "body": "",
            "created_utc": 10.0 + rank,
            "publisher": "Reuters",
            "category": category,
            "region": region,
            "rank": rank,
        }

    selected = poller.discover_x_topics(
        max_topics=3,
        headlines=[
            headline("ai", "Researchers release a frontier AI model", "technology", "US", 0),
            headline("chips", "South Korea expands HBM production", "business", "US", 1),
            headline("world", "Governments negotiate a global trade treaty", "world", "US", 0),
        ],
        trends=[],
    )

    assert [topic["selection_role"] for topic in selected] == [
        "strategic_technology", "strategic_technology", "major_global_fallback",
    ]
    assert selected[2]["external_id"] == "world"
    assert selected[2]["us_ranked_story"] is False


@pytest.mark.unit
def test_major_news_backfill_never_adds_a_third_technology_story():
    def headline(external_id, title, category, rank):
        return {
            "external_id": external_id,
            "title": f"{title} - Reuters",
            "body": "",
            "created_utc": 10.0 + rank,
            "publisher": "Reuters",
            "category": category,
            "region": "US",
            "rank": rank,
        }

    selected = poller.discover_x_topics(
        max_topics=5,
        headlines=[
            headline("ai", "Researchers release a frontier AI model", "technology", 0),
            headline("chips", "South Korea expands HBM production", "business", 1),
            headline("quantum", "Japan announces quantum computing breakthrough", "technology", 2),
            headline("us", "Federal courts issue a national election ruling", "us", 0),
            headline("world", "Countries agree a ceasefire in regional conflict", "world", 0),
        ],
        trends=[],
    )

    assert len(selected) == 4
    assert sum(topic["strategic_technology"] for topic in selected) == 2
    assert len(
        {topic["external_id"] for topic in selected}
        & {"ai", "chips", "quantum"}
    ) == 2
    assert {topic["selection_role"] for topic in selected[2:]} == {
        "major_us", "major_global",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "category", "strategic", "subdomain"),
    [
        ("China imposes semiconductor export controls", "world", True, "semiconductors"),
        ("US restricts AI chip exports to China", "world", True, "semiconductors"),
        ("China bans exports of rare earths used in chips", "world", True, "semiconductors"),
        ("Japan chipmaker expands processor production", "business", True, "semiconductors"),
        ("South Korean memory chip exports surge", "business", True, "semiconductors"),
        ("South Korea expands HBM production capacity", "business", True, "semiconductors"),
        ("Taiwan begins construction of advanced chip fab", "world", True, "semiconductors"),
        ("Researchers release a new foundation model", "technology", True, "artificial_intelligence"),
        (
            "OpenAI unveils GPT-5 AI model",
            "technology",
            True,
            "artificial_intelligence",
        ),
        ("Anthropic releases Claude 5 reasoning model", "technology", True, "artificial_intelligence"),
        ("DeepSeek releases R2 reasoning model", "technology", True, "artificial_intelligence"),
        ("Meta launches Llama 5 AI model", "technology", True, "artificial_intelligence"),
        ("Cyberattack disrupts national network infrastructure", "world", True, "cybersecurity"),
        ("Telecom operators announce network investment", "business", True, "telecommunications"),
        ("Telecom group begins 6G infrastructure research", "technology", True, "telecommunications"),
        ("Factory launches industrial robotics production line", "business", True, "robotics"),
        ("Japan funds a quantum computing breakthrough", "technology", True, "quantum"),
        ("Satellite launch expands orbital infrastructure", "technology", True, "space_infrastructure"),
        ("China election policy enters debate", "world", False, None),
        ("Presidential power faces a court challenge", "world", False, None),
        ("Fashion models launch summer collection", "technology", False, None),
        ("Researchers develop a new ocean model", "technology", False, None),
        ("Economists release a labor market model", "technology", False, None),
        ("Office space launches leasing campaign", "technology", False, None),
        ("New SUV model launches", "technology", False, None),
        ("Foldable phone launches with AI features", "technology", False, None),
        ("New generative AI phone launches as demand grows", "technology", False, None),
        ("Iran launches rockets at Israel", "world", False, None),
        ("Satellite images show attacks on military base", "world", False, None),
        ("Potato chip factory expands production", "business", False, None),
    ],
)
def test_strategic_technology_taxonomy_requires_technology_and_material_event(
    title, category, strategic, subdomain,
):
    result = poller._strategic_technology_classification(
        {"lineage": [{"title": title, "category": category}]}
    )

    assert result["strategic_technology"] is strategic
    if subdomain is not None:
        assert subdomain in result["strategic_subdomains"]


@pytest.mark.unit
def test_consumer_story_needs_a_systemic_technology_consequence():
    ordinary = poller._strategic_technology_classification({
        "lineage": [{"title": "New phone launches with AI camera features"}],
    })
    systemic = poller._strategic_technology_classification({
        "lineage": [{
            "title": "Phone production falls as semiconductor supply shortage expands"
        }],
    })

    assert ordinary == {
        "strategic_technology": False,
        "strategic_subdomains": [],
        "strategic_context": False,
        "major_global_impact": False,
        "us_ranked_story": False,
        "consumer_only": True,
    }
    assert systemic["strategic_technology"] is True
    assert systemic["consumer_only"] is False


@pytest.mark.unit
def test_discovery_normalization_matching_and_query_contract():
    assert poller._semantic_terms(
        "Nova AI models launch worldwide - Reuters"
    ) == {"nova", "ai", "model", "launch", "world"}
    assert poller._same_story(
        "Nova AI model launches worldwide - Reuters",
        "Nova AI models launched around world - BBC",
    ) is True
    assert poller._trend_matches_headline(
        "#OpenAI GPT6", "OpenAI launches GPT6 model"
    ) is True
    assert poller._trend_matches_headline(
        "#OpenAI Markets", "OpenAI launches model"
    ) is False
    assert poller._headline_query(
        "Breaking OpenAI GPT-6 Model Launches Worldwide - Reuters"
    ) == '"OpenAI GPT-6" Launches Model'


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "expected_query"),
    [
        ("Google launches Gemini 3", '"Gemini 3" Google launches'),
        ("OpenAI releases o3", "OpenAI o3 releases"),
        (
            "Anthropic unveils Claude 4",
            '"Claude 4" Anthropic unveils',
        ),
        (
            "Nvidia unveils Blackwell Ultra",
            '"Blackwell Ultra" Nvidia unveils',
        ),
    ],
)
def test_query_builder_preserves_opaque_named_subjects_and_events(
    title, expected_query,
):
    query = poller._headline_query(title)

    assert query == expected_query
    headline_words = set(re.findall(r"[a-z0-9]+", title.casefold()))
    query_words = set(re.findall(r"[a-z0-9]+", query.casefold()))
    assert query_words <= headline_words


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "category", "expected_query"),
    [
        (
            "US restricts AI chip exports to China",
            "world",
            'China "AI chip" exports',
        ),
        (
            "Israel and Iran agree ceasefire after US strikes",
            "world",
            "Israel Iran ceasefire",
        ),
        (
            "Trump announces tariffs on China",
            "world",
            "Trump China tariffs",
        ),
    ],
)
def test_discovery_queries_preserve_named_subjects_and_material_events(
    title, category, expected_query,
):
    topics = poller.discover_x_topics(
        max_topics=1,
        headlines=[{
            "external_id": "headline",
            "title": f"{title} - Reuters",
            "body": "",
            "created_utc": 10.0,
            "publisher": "Reuters",
            "category": category,
            "region": "US",
            "rank": 0,
        }],
        trends=[],
    )

    assert len(topics) == 1
    assert topics[0]["query"] == expected_query
    headline_words = set(re.findall(r"[a-z0-9]+", title.casefold()))
    query_words = set(re.findall(r"[a-z0-9]+", expected_query.casefold()))
    assert query_words <= headline_words


@pytest.mark.unit
def test_opaque_model_name_requires_an_explicit_strategic_domain():
    def topics(title):
        return poller.discover_x_topics(
            max_topics=1,
            headlines=[{
                "external_id": "headline",
                "title": f"{title} - Reuters",
                "body": "",
                "created_utc": 10.0,
                "publisher": "Reuters",
                "category": "technology",
                "region": "US",
                "rank": 0,
            }],
            trends=[],
        )

    assert topics("Google launches Gemini 3") == []
    selected = topics("Google launches AI model Gemini 3")
    assert len(selected) == 1
    assert selected[0]["strategic_subdomains"] == ["artificial_intelligence"]
    assert "gemini 3" in selected[0]["query"].casefold()


@pytest.mark.unit
@pytest.mark.parametrize(
    "title",
    [
        "Phone maker launches Nova 4 smartphone",
        "Fashion house launches Aurora 4 collection",
        "Automaker unveils Falcon 4 vehicle",
        "ZephyrQuill launches Aurora 4 software platform",
        "Tesla unveils Model Y",
        "Boeing launches MAX-10",
        "Samsung unveils Galaxy S26",
        "Microsoft launches Windows 12",
        "Ford unveils Mustang Mach E",
        "Netflix releases Stranger Things 6",
    ],
)
def test_named_release_discovery_rejects_non_strategic_products(title):
    topics = poller.discover_x_topics(
        max_topics=1,
        headlines=[{
            "external_id": "headline",
            "title": f"{title} - Reuters",
            "body": "",
            "created_utc": 10.0,
            "publisher": "Reuters",
            "category": "technology",
            "region": "US",
            "rank": 0,
        }],
        trends=[],
    )

    assert topics == []


@pytest.mark.unit
def test_discovery_query_order_is_stable_across_python_hash_seeds():
    script = (
        "from tradingagents.poller import _headline_query; "
        "print(_headline_query("
        "'US and AI model launches worldwide - Reuters', "
        "domain_signals=('AI model',)))"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("1", "7", "113")
    }

    assert outputs == {'US "AI model" launches'}


@pytest.mark.unit
def test_grouped_story_uses_the_role_qualifying_headline_for_its_query():
    headlines = [
        {
            "external_id": "generic",
            "title": "US China trade restrictions expand - Reuters",
            "body": "",
            "created_utc": 10.0,
            "publisher": "Reuters",
            "category": "world",
            "region": "US",
            "rank": 0,
        },
        {
            "external_id": "semiconductor",
            "title": "US China semiconductor trade restrictions expand - BBC",
            "body": "",
            "created_utc": 11.0,
            "publisher": "BBC",
            "category": "technology",
            "region": "GB",
            "rank": 1,
        },
    ]

    selected = poller.discover_x_topics(
        max_topics=1, headlines=headlines, trends=[]
    )[0]

    assert selected["external_id"] == "semiconductor"
    assert selected["title"] == headlines[1]["title"]
    assert "semiconductor" in selected["query"].casefold()
    assert selected["strategic_technology"] is True


@pytest.mark.unit
def test_strategic_query_keeps_technology_and_east_asia_context():
    headline = {
        "external_id": "hbm",
        "title": "South Korea expands HBM production capacity - Reuters",
        "body": "",
        "created_utc": 10.0,
        "publisher": "Reuters",
        "category": "business",
        "region": "US",
        "rank": 0,
    }

    query = poller.discover_x_topics(
        max_topics=1, headlines=[headline], trends=[]
    )[0]["query"].casefold()

    assert "hbm" in query
    assert "south korea" in query


@pytest.mark.unit
def test_general_feed_requires_global_impact_and_technology_noise_cannot_backfill():
    general_policy = {
        "external_id": "policy",
        "title": "National administration announces new tariff policy - Reuters",
        "body": "",
        "created_utc": 10.0,
        "publisher": "Reuters",
        "category": "general",
        "region": "US",
        "rank": 0,
    }
    generic_public_story = {
        **general_policy,
        "external_id": "generic",
        "title": "Major public story develops - Reuters",
    }
    product_noise = {
        **general_policy,
        "external_id": "product",
        "title": "ZephyrQuill launches a software platform - Reuters",
        "category": "technology",
    }

    selected = poller.discover_x_topics(
        max_topics=3,
        headlines=[generic_public_story, product_noise, general_policy],
        trends=[],
    )

    assert [item["external_id"] for item in selected] == ["policy"]
    assert selected[0]["selection_role"] == "major_global_fallback"


@pytest.mark.unit
def test_discovery_query_identity_is_casefolded_and_whitespace_stable():
    assert poller._discovery_query_identity('  "NovaX"   AI model ') == (
        poller._discovery_query_identity('"NOVAX" AI model')
    )


@pytest.mark.unit
def test_subdomain_diversity_never_displaces_materially_stronger_reporting():
    def headline(external_id, title, publisher, domain, rank):
        return {
            "external_id": external_id,
            "title": f"{title} - {publisher}",
            "body": "",
            "created_utc": 10.0 + rank,
            "publisher": publisher,
            "category": "technology",
            "region": "US",
            "rank": rank,
            "metadata": {"publisher_domain": domain},
        }

    headlines = [
        *[
            headline(
                f"biology-{index}",
                "Atlas releases frontier AI model for genomic medicine",
                publisher,
                domain,
                index,
            )
            for index, (publisher, domain) in enumerate((
                ("Reuters", "reuters.com"),
                ("BBC", "bbc.co.uk"),
                ("NPR", "npr.org"),
            ))
        ],
        *[
            headline(
                f"weather-{index}",
                "Nova deploys reasoning model for weather prediction",
                publisher,
                domain,
                index + 1,
            )
            for index, (publisher, domain) in enumerate((
                ("Reuters", "reuters.com"),
                ("BBC", "bbc.co.uk"),
            ))
        ],
        headline(
            "quantum",
            "Researchers announce quantum computing breakthrough",
            "NPR",
            "npr.org",
            0,
        ),
    ]

    selected = poller.discover_x_topics(
        max_topics=2, headlines=headlines, trends=[]
    )

    assert [item["external_id"].split("-")[0] for item in selected] == [
        "biology", "weather",
    ]
    assert all(
        item["strategic_subdomains"] == ["artificial_intelligence"]
        for item in selected
    )


@pytest.mark.unit
def test_discovery_clusters_headline_variants_and_keeps_lineage(monkeypatch):
    headlines = [
        {"external_id": "one", "title": "Nova AI model launches worldwide - Reuters",
         "body": "", "created_utc": 10.0, "publisher": "Reuters", "category": "technology",
         "region": "US", "rank": 0},
        {"external_id": "two", "title": "Nova AI model launched around world - BBC",
         "body": "", "created_utc": 11.0, "publisher": "BBC", "category": "world",
         "region": "GB", "rank": 1},
    ]
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: headlines
    )
    monkeypatch.setattr(poller, "fetch_x_trends", lambda _, **_kwargs: [])

    topics = poller.discover_x_topics(max_topics=1)

    assert len(topics) == 1
    assert {row["external_id"] for row in topics[0]["lineage"]} == {"one", "two"}
    assert topics[0]["regions"] == {"US", "GB"}


@pytest.mark.unit
def test_discovery_never_spends_search_budget_on_general_only_topic():
    headlines = [{
        "external_id": "general-1",
        "title": "Major public story develops - Reuters",
        "body": "",
        "created_utc": 10.0,
        "publisher": "Reuters",
        "category": "general",
        "region": "US",
        "rank": 0,
    }]

    assert poller.discover_x_topics(
        max_topics=3, headlines=headlines, trends=[]
    ) == []


@pytest.mark.unit
def test_paid_topic_search_requires_formally_independent_editorial_lineage():
    captured = 1_800_000_000.0
    base = {
        "topic": "trend_technology", "category": "technology",
        "query": '"Nova" model', "external_id": "story",
        "title": "Nova model launches", "body": "report",
        "created_utc": captured - 10,
    }
    sponsored_capable = {
        **base,
        "publisher": "TechCrunch",
        "metadata": {"publisher_domain": "techcrunch.com"},
    }
    independent = {
        **base,
        "external_id": "independent",
        "publisher": "Reuters",
        "metadata": {"publisher_domain": "reuters.com"},
    }

    assert poller._formally_grounded_discovery_topics(
        [sponsored_capable], captured
    ) == []
    assert poller._formally_grounded_discovery_topics(
        [independent], captured
    ) == [independent]

    stale = {**independent, "created_utc": captured - 7 * 86400 - 1}
    future = {**independent, "created_utc": captured + 1}
    missing_identity = {**independent, "external_id": ""}
    corporate = {
        **independent,
        "metadata": {
            **independent["metadata"],
            "verified_type": "business",
        },
    }
    assert poller._formally_grounded_discovery_topics(
        [stale, future, missing_identity, corporate], captured
    ) == []


@pytest.mark.unit
def test_paid_topic_search_requires_a_finite_capture_time():
    with pytest.raises(ValueError, match="capture time must be finite"):
        poller._formally_grounded_discovery_topics([], float("nan"))


@pytest.mark.unit
def test_discovery_filters_ineligible_publishers_before_allocating_slots():
    captured = 1_800_000_000.0

    def headline(
        external_id, title, publisher, domain, category, rank,
    ):
        return {
            "external_id": external_id,
            "title": title,
            "body": "independent report",
            "created_utc": captured - 60,
            "publisher": publisher,
            "category": category,
            "region": "US",
            "rank": rank,
            "metadata": {"publisher_domain": domain},
        }

    topics = poller.discover_x_topics(
        max_topics=3,
        captured_utc=captured,
        trends=[],
        headlines=[
            headline(
                "ineligible",
                "Researchers release a frontier AI model",
                "The Verge",
                "theverge.com",
                "technology",
                0,
            ),
            headline(
                "ai",
                "Researchers release a foundation AI model",
                "Reuters",
                "reuters.com",
                "technology",
                1,
            ),
            headline(
                "chips",
                "South Korea expands HBM production capacity",
                "Associated Press",
                "apnews.com",
                "business",
                2,
            ),
            headline(
                "global",
                "Governments announce a new global trade agreement",
                "BBC",
                "bbc.co.uk",
                "world",
                0,
            ),
        ],
    )

    assert [topic["external_id"] for topic in topics] == [
        "chips", "ai", "global",
    ]
    assert all(
        headline["publisher"] in {"Reuters", "Associated Press", "BBC"}
        for topic in topics for headline in topic["lineage"]
    )


@pytest.mark.unit
def test_x_discovery_cycle_has_independent_daily_clock(tmp_path, monkeypatch):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "media.db")
    topic = {
        "topic": "trend_world", "category": "world", "query": '"Bordeaux" wildfires',
        "external_id": "headline-1", "title": "Wildfires force Bordeaux evacuations - Reuters",
        "body": "summary", "created_utc": now - 10, "publisher": "Reuters",
        "metadata": {
            "article_url": "https://news.google.com/articles/headline-1",
            "publisher_domain": "reuters.com",
        },
    }
    trend_limits = []

    def fetch_trends(_woeid, *, limit):
        trend_limits.append(limit)
        return [{"name": "Global event"}]

    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(poller, "fetch_x_trends", fetch_trends)
    monkeypatch.setattr(
        poller, "discover_x_topics", lambda max_topics, **kwargs: [topic]
    )
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: [topic]
    )
    monkeypatch.setattr(
        poller, "fetch_x_topic",
        lambda topic, query, now, limit: [
            _row("x", "post-1", f"@{topic}", now, created_utc=now, body=query)
        ],
    )

    poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)

    assert store.get_meta("last_x_poll_utc") == now
    stats = {(row[0], row[1]) for row in store.stats()}
    assert ("@TREND_WORLD", "x") in stats
    assert ("@TREND_WORLD", "trendnews") in stats
    trend_receipts = store.fetch_runs(provider="xtrend")
    search_receipts = store.fetch_runs(provider="x")
    assert len(trend_receipts) == 2
    assert len(search_receipts) == 1
    assert trend_limits == [
        media_sources.GLOBAL_X_ADAPTER_POLICY["trends"]["result_limit"][
            "default"
        ]
    ] * len(trend_receipts)
    x_items = store.fetch_items(search_receipts[0]["fetch_run_id"])
    assert len(x_items) == 1
    assert x_items[0]["source"] == "x"
    assert x_items[0]["external_id"] == "post-1"
    assert x_items[0]["observed_utc"] == search_receipts[0]["received_utc"]
    assert x_items[0]["raw_content_id"].startswith("raw_")
    assert search_receipts[0]["formal_eligible_lineage"] == []
    for receipt in trend_receipts + search_receipts:
        assert receipt["cost_units"] == 1.0
        assert "budget_reservation" in receipt["metadata_json"]
    cycle_ids = {
        receipt["collection_cycle_id"]
        for receipt in trend_receipts + search_receipts
    }
    assert len(cycle_ids) == 1
    cycle = store.collection_cycle(cycle_ids.pop())
    assert cycle["status"] == "complete"
    assert poller._x_daily_requirement_state(store, now, _X_TOPIC_LIMIT) == "complete"
    next_midnight = datetime(2026, 8, 6, tzinfo=timezone.utc).timestamp()
    assert poller._x_daily_requirement_state(store, next_midnight, _X_TOPIC_LIMIT) == "scheduled"
    assert cycle["manifest_valid"] is True
    assert {
        row["status"] for row in cycle["manifest"]["slot_receipts"]
    } == {"success"}
    manifest_receipts = {
        (row["provider"], row["query_key"]): row
        for row in cycle["manifest"]["slot_receipts"]
    }
    for receipt in trend_receipts:
        items = store.fetch_items(receipt["fetch_run_id"])
        assert items
        assert manifest_receipts[(receipt["provider"], receipt["query_key"])][
            "raw_content_ids"
        ] == sorted(item["raw_content_id"] for item in items)
    tampered_item = store.fetch_items(trend_receipts[0]["fetch_run_id"])[0]
    store.conn.execute(
        "UPDATE media_posts SET body='tampered trend response' "
        "WHERE source=? AND external_id=?",
        (tampered_item["source"], tampered_item["external_id"]),
    )
    store.conn.commit()
    with pytest.raises(ValueError, match="raw-content replay mismatch"):
        store.collection_cycle(cycle["collection_cycle_id"])
    store.close()


@pytest.mark.unit
def test_observed_empty_x_search_is_valid_cycle_coverage(tmp_path, monkeypatch):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "media.db")
    topic = {
        "topic": "trend_world", "category": "world", "query": '"Global event" reaction',
        "external_id": "headline-1", "title": "Global event develops - Reuters",
        "body": "summary", "created_utc": now - 10, "publisher": "Reuters",
        "metadata": {
            "article_url": "https://news.google.com/articles/headline-1",
            "publisher_domain": "reuters.com",
        },
    }
    alerts = []
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller,
        "fetch_x_trends",
        lambda woeid, **_kwargs: [{"name": "Global event"}],
    )
    monkeypatch.setattr(
        poller, "discover_x_topics", lambda max_topics, **kwargs: [topic]
    )
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: [topic]
    )
    monkeypatch.setattr(poller, "fetch_x_topic", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        poller,
        "emit_alert",
        lambda component, event, **kwargs: alerts.append((component, event, kwargs)),
    )

    poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )

    assert alerts == []
    assert store.get_meta("poller:last_success_utc") == now
    assert store.get_meta("poller:last_failure_utc") is None
    cycle_id = poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)["collection_cycle_id"]
    cycle = store.collection_cycle(cycle_id)
    assert cycle["status"] == "complete"
    outcomes = {
        (row["provider"], row["query_key"]): row["status"]
        for row in cycle["manifest"]["slot_receipts"]
    }
    assert outcomes[("x", topic["query"])] == "empty"
    store.close()


@pytest.mark.unit
def test_x_source_enables_discovery_not_per_ticker_queries(tmp_path, monkeypatch):
    captured = {}

    def fake_run_cycle(store, tickers, sources, macro_themes, x_enabled,
                       x_interval, x_limit, x_topic_limit, force_x):
        captured.update(
            tickers=tickers,
            sources=sources,
            x_enabled=x_enabled,
            x_interval=x_interval,
            x_limit=x_limit,
            x_topic_limit=x_topic_limit,
            force_x=force_x,
        )

    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(poller, "run_cycle", fake_run_cycle)

    poller.main([
        "--tickers", "AAPL,NVDA",
        "--sources", "x",
        "--once",
        "--no-macro",
        "--db", str(tmp_path / "media.db"),
    ])

    assert captured["tickers"] == ["AAPL", "NVDA"]
    assert captured["sources"] == []
    assert captured["x_enabled"] is True
    assert captured["x_interval"] == 86400
    assert captured["x_topic_limit"] == _X_TOPIC_LIMIT
    assert captured["x_limit"] == 10
    assert captured["force_x"] is True


@pytest.mark.unit
def test_global_only_mode_runs_news_and_bounded_x_without_ticker_sources(
    tmp_path, monkeypatch,
):
    captured = {}

    def fake_run_cycle(store, tickers, sources, macro_themes, x_enabled,
                       x_interval, x_limit, x_topic_limit, force_x):
        captured.update(
            tickers=tickers,
            sources=sources,
            macro_themes=macro_themes,
            x_enabled=x_enabled,
            x_interval=x_interval,
            x_limit=x_limit,
            x_topic_limit=x_topic_limit,
            force_x=force_x,
        )

    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setattr(poller, "run_cycle", fake_run_cycle)

    poller.main([
        "--global-only",
        "--sources", "x",
        "--once",
        "--no-trading-hours",
        "--interval", "3600",
        "--x-interval", "86400",
        "--db", str(tmp_path / "global.db"),
    ])

    assert captured["tickers"] == []
    assert captured["sources"] == []
    assert captured["x_enabled"] is True
    assert captured["x_interval"] == 86400
    assert captured["x_topic_limit"] == _X_TOPIC_LIMIT
    assert captured["x_limit"] == 10
    assert captured["force_x"] is True
    assert len(poller._globalnews_query_slots(captured["macro_themes"])) == 10
    assert all(
        spec["prediction_topics"] == []
        for spec in captured["macro_themes"].values()
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra_args",
    [
        ["--tickers", "AAPL"],
        ["--sources", "x,reddit"],
        ["--no-macro"],
    ],
)
def test_global_only_mode_rejects_tickers_ticker_sources_and_missing_news(
    tmp_path, monkeypatch, extra_args,
):
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    base = [
        "--global-only",
        "--sources", "x",
        "--once",
        "--no-trading-hours",
        "--interval", "3600",
        "--x-interval", "86400",
        "--db", str(tmp_path / "must-not-open.db"),
    ]
    if "--sources" in extra_args:
        base[base.index("--sources") + 1] = extra_args[1]
        extra_args = []

    with pytest.raises(SystemExit):
        poller.main([*base, *extra_args])


@pytest.mark.unit
def test_global_only_mode_requires_explicit_versioned_cadence(tmp_path, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.delenv("MEDIA_POLLER_INTERVAL", raising=False)
    monkeypatch.delenv("MEDIA_POLLER_X_INTERVAL", raising=False)

    with pytest.raises(SystemExit):
        poller.main([
            "--global-only",
            "--sources", "x",
            "--once",
            "--no-trading-hours",
            "--db", str(tmp_path / "missing-cadence.db"),
        ])


@pytest.mark.unit
def test_global_only_daemon_fails_closed_while_collection_is_paused(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("X_BEARER_TOKEN", "secret-test-token")
    monkeypatch.setenv("MEDIA_COLLECTION_ENABLED", "false")
    monkeypatch.setattr(
        poller,
        "open_store",
        lambda *_args, **_kwargs: pytest.fail("paused daemon opened the database"),
    )

    with pytest.raises(SystemExit):
        poller.main([
            "--global-only",
            "--sources", "x",
            "--no-trading-hours",
            "--interval", "3600",
            "--x-interval", "86400",
            "--db", str(tmp_path / "paused.db"),
        ])


@pytest.mark.unit
def test_explicit_x_source_fails_startup_without_nonblank_credentials(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("X_BEARER_TOKEN", "   ")

    with pytest.raises(SystemExit):
        poller.main([
            "--sources", "x", "--once", "--no-macro",
            "--db", str(tmp_path / "missing-token.db"),
        ])


@pytest.mark.unit
def test_x_cycle_manifest_distinguishes_failed_trend_and_empty_search(
    tmp_path, monkeypatch,
):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-failure.db")
    topic = {
        "topic": "trend_world",
        "category": "world",
        "query": '"Global event" reaction',
        "external_id": "headline-1",
        "title": "Global event develops - Reuters",
        "body": "summary",
        "created_utc": now - 10,
        "publisher": "Reuters",
        "metadata": {
            "article_url": "https://news.google.com/articles/headline-1",
            "publisher_domain": "reuters.com",
        },
    }
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: [topic]
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_trends",
        lambda woeid, **_kwargs: (_ for _ in ()).throw(
            poller.ProviderTransientError("unavailable")
        )
        if woeid == 1 else [{"name": "Global event"}],
    )
    monkeypatch.setattr(
        poller, "discover_x_topics", lambda max_topics, **kwargs: [topic]
    )
    monkeypatch.setattr(poller, "fetch_x_topic", lambda *args, **kwargs: [])

    poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)

    cycle_id = poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)["collection_cycle_id"]
    cycle = store.collection_cycle(cycle_id)
    outcomes = {
        (row["provider"], row["query_key"]): row["status"]
        for row in cycle["manifest"]["slot_receipts"]
    }
    assert cycle["status"] == "incomplete"
    assert outcomes[("xtrend", "woeid:1")] == "failed"
    assert outcomes[("xtrend", "woeid:23424977")] == "missing"
    assert outcomes[("trendnews", "ranked-global-discovery")] == "missing"
    assert all(provider != "x" for provider, _ in outcomes)
    assert poller._x_daily_requirement_state(store, now, _X_TOPIC_LIMIT) == "incomplete"

    alerts = []
    monkeypatch.setattr(
        poller,
        "emit_alert",
        lambda component, event, **kwargs: alerts.append(
            (component, event, kwargs)
        ) or True,
    )
    coverage = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
    )
    assert coverage["complete"] is False
    assert coverage["missing_query_slots"] == []
    assert coverage["periodic_requirements"] == {"x_daily": "incomplete"}
    assert coverage["missing_periodic_requirements"] == ["x_daily"]
    assert alerts[0][1] == "query_slot_coverage_incomplete"
    assert alerts[0][2]["details"]["missing_x_daily_requirement"] is True
    store.close()


@pytest.mark.unit
def test_discovery_feed_failure_never_starts_or_spends_paid_x_cycle(
    tmp_path, monkeypatch,
):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-news-partial.db")
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller,
        "fetch_x_trends",
        lambda _, **_kwargs: pytest.fail(
            "free discovery must be validated before paid X"
        ),
    )
    monkeypatch.setattr(
        poller,
        "fetch_top_news_headlines",
        lambda **_kwargs: (_ for _ in ()).throw(
            poller.ProviderTransientError("top-news discovery feed set was incomplete")
        ),
    )

    slots = poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)

    cycle_id = poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)["collection_cycle_id"]
    assert store.collection_cycle(cycle_id) is None
    assert store.fetch_runs(limit=100) == []
    day_start = datetime(2026, 8, 5, tzinfo=timezone.utc).timestamp()
    assert store.daily_cost_units("xtrend", day_start, day_start + 86400) == 0.0
    assert set(slots) == {
        ("xtrend", "woeid:1"),
        ("xtrend", "woeid:23424977"),
        ("trendnews", "ranked-global-discovery"),
    }
    assert all(provider != "x" for provider, _ in slots)

    alerts = []
    monkeypatch.setattr(
        poller,
        "emit_alert",
        lambda component, event, **kwargs: alerts.append(
            (component, event, kwargs)
        ) or True,
    )
    coverage = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )
    assert coverage["complete"] is False
    assert coverage["periodic_requirements"] == {"x_daily": "missing"}
    assert coverage["missing_periodic_requirements"] == ["x_daily"]
    assert alerts[0][2]["details"] == {
        "expected_query_slot_count": 0,
        "missing_query_slot_count": 0,
        "missing_periodic_requirement_count": 1,
        "missing_x_daily_requirement": True,
        "x_daily_state": "missing",
        "missing_source_group_count": 0,
        "reason_counts": {},
        "slots": [],
        "slots_truncated": 0,
    }
    poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )
    assert len(alerts) == 1
    store.close()


@pytest.mark.unit
def test_same_daily_x_cycle_cannot_retry_paid_requests(tmp_path, monkeypatch):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-no-retry.db")
    topic = {
        "topic": "trend_technology",
        "category": "technology",
        "query": '"Nova model" launch',
        "external_id": "headline-1",
        "title": "Nova model launches - Reuters",
        "body": "summary",
        "created_utc": now - 10,
        "publisher": "Reuters",
        "metadata": {
            "article_url": "https://news.google.com/articles/headline-1",
            "publisher_domain": "reuters.com",
        },
    }
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: [topic]
    )
    monkeypatch.setattr(poller, "fetch_x_trends", lambda _, **_kwargs: [])
    monkeypatch.setattr(
        poller, "discover_x_topics", lambda max_topics, **kwargs: [topic]
    )
    monkeypatch.setattr(poller, "fetch_x_topic", lambda *args, **kwargs: [])

    poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)
    first_receipts = store.fetch_runs(limit=100)
    monkeypatch.setattr(
        poller, "fetch_x_trends",
        lambda _, **_kwargs: pytest.fail(
            "terminal cycle reuse must not fetch trends"
        ),
    )
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines",
        lambda **_kwargs: pytest.fail("terminal cycle reuse must not fetch news"),
    )
    monkeypatch.setattr(
        poller, "fetch_x_topic",
        lambda *args, **kwargs: pytest.fail("terminal cycle reuse must not search"),
    )
    reused_slots = poller.poll_x_topics_once(
        store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT
    )

    assert len(store.fetch_runs(limit=100)) == len(first_receipts)
    assert ("x", topic["query"]) in reused_slots
    day_start = datetime(2026, 8, 5, tzinfo=timezone.utc).timestamp()
    assert store.daily_cost_units("xtrend", day_start, day_start + 86400) == 2.0
    assert store.daily_cost_units("x", day_start, day_start + 86400) == 1.0
    store.close()


@pytest.mark.unit
def test_mid_cycle_crash_terminalizes_the_cycle_and_blocks_paid_retries(
    tmp_path, monkeypatch,
):
    """Regression for the 2026-08-23 incident: a ValueError inside the
    discovery stage crashed the collector after the paid trend fetches. The
    cycle must land terminally incomplete (never left running), the paid
    receipts must survive, and a same-day rerun must spend nothing."""
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-crash.db")
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller,
        "fetch_top_news_headlines",
        lambda **_kwargs: [{
            "category": "world",
            "external_id": "story-1",
            "title": "Major event changes outlook - Reuters",
            "created_utc": now - 10,
            "publisher": "Reuters",
            "metadata": {"publisher_domain": "reuters.com"},
        }],
    )
    monkeypatch.setattr(
        poller, "fetch_x_trends", lambda *_args, **_kwargs: [{"name": "Major event"}]
    )
    monkeypatch.setattr(
        poller,
        "discover_x_topics",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("stage invariant violated")
        ),
    )

    with pytest.raises(ValueError, match="stage invariant violated"):
        poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)

    cycle_id = poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)["collection_cycle_id"]
    cycle = store.collection_cycle(cycle_id)
    assert cycle["status"] == "incomplete"
    trend_receipts = store.fetch_runs(provider="xtrend")
    assert trend_receipts and all(
        receipt["status"] in {"success", "empty"} for receipt in trend_receipts
    )

    for paid in ("fetch_x_trends", "fetch_x_topic", "fetch_x_recent_counts"):
        monkeypatch.setattr(
            poller, paid,
            lambda *_args, **_kwargs: pytest.fail("terminal day must not spend"),
        )
    monkeypatch.setattr(
        poller,
        "discover_x_topics",
        lambda *_args, **_kwargs: pytest.fail("terminal day must not rediscover"),
    )
    slots = poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)
    assert ("xtrend", "woeid:1") in slots
    assert len(store.fetch_runs(limit=100)) == len(trend_receipts) + 1  # + failed discovery
    store.close()


@pytest.mark.unit
def test_duplicate_derived_queries_form_one_bound_request_with_all_labels(
    tmp_path, monkeypatch,
):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-grouped-query.db")
    topics = [
        {
            "topic": "trend_world",
            "category": "world",
            "query": '"Shared event" reaction',
            "external_id": "world-story",
            "title": "Shared event changes world outlook - Reuters",
            "created_utc": now - 10,
            "publisher": "Reuters",
            "metadata": {"publisher_domain": "reuters.com"},
        },
        {
            "topic": "trend_business",
            "category": "business",
            "query": '"Shared event" reaction',
            "external_id": "business-story",
            "title": "Shared event changes markets - Reuters",
            "created_utc": now - 9,
            "publisher": "Reuters",
            "metadata": {"publisher_domain": "reuters.com"},
        },
    ]
    calls = []
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: topics
    )
    monkeypatch.setattr(poller, "fetch_x_trends", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        poller, "discover_x_topics", lambda max_topics, **_kwargs: topics
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_topic",
        lambda topic, query, now, limit: calls.append(
            (topic, query, now, limit)
        ) or [],
    )

    poller.poll_x_topics_once(store, now=now, limit=10, max_topics=_X_TOPIC_LIMIT)

    cycle_id = poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)["collection_cycle_id"]
    cycle = store.collection_cycle(cycle_id)
    assert calls == [("trend_business", '"Shared event" reaction', now, 10)]
    assert cycle["status"] == "complete"
    assert cycle["manifest"]["expected_dynamic_slots"] == [{
        "provider": "x",
        "query_key": '"Shared event" reaction',
    }]
    receipt = store.fetch_runs(provider="x")[0]
    assert json.loads(receipt["metadata_json"])["labels"] == [
        "@TREND_BUSINESS",
        "@TREND_WORLD",
    ]
    decision_items = store.collection_cycle_item_rows(
        cycle_id,
        provider="trendnews",
        query_key="ranked-global-discovery",
    )
    decisions = [
        json.loads(item["row"]["body"])
        for item in decision_items
        if item["row"].get("ticker") == "@X_DISCOVERY_AUDIT"
    ]
    assert len(decisions) == 1
    poller.validate_x_discovery_decision(decisions[0])
    assert decisions[0]["search_requests"][0]["labels"] == [
        "@TREND_BUSINESS",
        "@TREND_WORLD",
    ]
    store.close()


@pytest.mark.unit
def test_restart_recovers_running_daily_cycle_without_an_external_retry(
    tmp_path, monkeypatch,
):
    store = SqliteMediaStore(tmp_path / "x-recovery.db")
    clock = {"now": 101.0}
    monkeypatch.setattr(poller.time, "time", lambda: clock["now"])
    spec = poller._x_collection_cycle_spec(100.0, _X_TOPIC_LIMIT)
    cycle_id = store.start_collection_cycle(spec, started_utc=-1000.0)
    orphan = store.start_budgeted_fetch(
        "xtrend",
        "woeid:1",
        -999.0,
        collection_cycle_id=cycle_id,
        budget_limits={"x-budget:trend:1970-01-01:total": 2.0},
        metadata={"kind": "media", "budget_category": "trend"},
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_trends",
        lambda _, **_kwargs: pytest.fail(
            "recovery must not retry a paid trend request"
        ),
    )
    monkeypatch.setattr(
        poller,
        "fetch_top_news_headlines",
        lambda **_kwargs: pytest.fail("recovery must not rerun discovery"),
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_topic",
        lambda *args, **kwargs: pytest.fail("recovery must not retry paid search"),
    )
    clock["now"] += float(
        poller.GLOBAL_EVENT_V2_PROTOCOL["evidence"][
            "x_cycle_recovery_stale_seconds"
        ]
    ) + 1.0

    slots = poller.poll_x_topics_once(store, now=100.0, limit=10, max_topics=_X_TOPIC_LIMIT)

    cycle = store.collection_cycle(cycle_id)
    orphan_receipt = next(
        row for row in store.fetch_runs(limit=100) if row["fetch_run_id"] == orphan
    )
    outcomes = {
        (row["provider"], row["query_key"]): row["status"]
        for row in cycle["manifest"]["slot_receipts"]
    }
    assert cycle["status"] == "incomplete"
    assert orphan_receipt["status"] == "failed"
    assert orphan_receipt["error"] == "collector_restart_recovery"
    assert outcomes[("xtrend", "woeid:1")] == "failed"
    assert outcomes[("xtrend", "woeid:23424977")] == "missing"
    assert outcomes[("trendnews", "ranked-global-discovery")] == "missing"
    assert set(slots) == {
        ("xtrend", "woeid:1"),
        ("xtrend", "woeid:23424977"),
        ("trendnews", "ranked-global-discovery"),
    }
    assert store.get_meta("last_x_poll_utc") == 100.0
    assert store.get_meta("x-budget:trend:1970-01-01:total") == 1.0
    store.close()


@pytest.mark.unit
def test_concurrent_contender_noops_while_daily_cycle_owner_is_fresh(
    tmp_path, monkeypatch,
):
    store = SqliteMediaStore(tmp_path / "x-fresh-owner.db")
    monkeypatch.setattr(poller.time, "time", lambda: 101.0)
    spec = poller._x_collection_cycle_spec(100.0, _X_TOPIC_LIMIT)
    cycle_id = store.start_collection_cycle(spec, started_utc=100.0)
    owner_receipt = store.start_budgeted_fetch(
        "xtrend",
        "woeid:1",
        100.5,
        collection_cycle_id=cycle_id,
        budget_limits={"x-budget:fresh-owner": 1.0},
        metadata={"kind": "media", "budget_category": "trend"},
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_trends",
        lambda _, **_kwargs: pytest.fail(
            "a contender must not issue an external request"
        ),
    )

    slots = poller.poll_x_topics_once(store, now=100.0, limit=10, max_topics=_X_TOPIC_LIMIT)

    cycle = store.collection_cycle(cycle_id)
    receipt = next(
        row for row in store.fetch_runs(limit=100)
        if row["fetch_run_id"] == owner_receipt
    )
    assert cycle["status"] == "running"
    assert receipt["status"] == "running"
    assert store.get_meta("x-budget:fresh-owner") == 1.0
    assert store.get_meta("last_x_poll_utc") is None
    assert set(slots) == {
        ("xtrend", "woeid:1"),
        ("xtrend", "woeid:23424977"),
        ("trendnews", "ranked-global-discovery"),
    }
    store.close()


@pytest.mark.unit
def test_receiptless_x_period_is_incomplete_and_utc_date_keyed(tmp_path, monkeypatch):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-period.db")
    monkeypatch.setattr(poller.time, "time", lambda: now)
    spec = poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)
    cycle_id = store.start_collection_cycle(spec, started_utc=now)
    store.finish_collection_cycle(cycle_id, completed_utc=now + 1)

    assert poller._x_daily_requirement_state(store, now, _X_TOPIC_LIMIT) == "incomplete"
    next_midnight = datetime(2026, 8, 6, tzinfo=timezone.utc).timestamp()
    assert poller._x_daily_requirement_state(store, next_midnight, _X_TOPIC_LIMIT) == "scheduled"
    next_closed = datetime(2026, 8, 6, 23, 46, tzinfo=timezone.utc).timestamp()
    assert poller._x_daily_requirement_state(store, next_closed, _X_TOPIC_LIMIT) == "missing"
    with pytest.raises(ValueError, match="frozen protocol"):
        poller.run_cycle(
            store,
            tickers=[],
            sources=[],
            macro_themes={},
            x_enabled=True,
            x_interval=86399,
        )
    store.close()


@pytest.mark.unit
def test_x_cycle_is_scheduled_before_window_even_when_forced(tmp_path, monkeypatch):
    now = datetime(2026, 8, 5, 20, 59, tzinfo=timezone.utc).timestamp()
    store = SqliteMediaStore(tmp_path / "x-before-window.db")
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller,
        "fetch_top_news_headlines",
        lambda **_kwargs: pytest.fail("scheduled X must not fetch discovery input"),
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_trends",
        lambda *_args, **_kwargs: pytest.fail("scheduled X must not fetch trends"),
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_topic",
        lambda *_args, **_kwargs: pytest.fail("scheduled X must not search"),
    )
    alerts = []
    monkeypatch.setattr(
        poller,
        "emit_alert",
        lambda *args, **kwargs: alerts.append((args, kwargs)) or True,
    )

    coverage = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )

    assert coverage["periodic_requirements"] == {"x_daily": "scheduled"}
    assert coverage["missing_periodic_requirements"] == []
    assert coverage["complete"] is True
    assert coverage["query_slots"] == []
    assert store.fetch_runs() == []
    assert alerts == []
    store.close()


@pytest.mark.unit
def test_x_cycle_does_not_start_near_utc_midnight_and_reports_missing(
    tmp_path, monkeypatch,
):
    now = _X_WINDOW_CLOSED_UTC
    store = SqliteMediaStore(tmp_path / "x-midnight-boundary.db")
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller,
        "fetch_top_news_headlines",
        lambda **_kwargs: pytest.fail("late cycle must not fetch discovery input"),
    )
    alerts = []
    monkeypatch.setattr(
        poller,
        "emit_alert",
        lambda *args, **kwargs: alerts.append((args, kwargs)) or True,
    )

    coverage = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )

    assert store.fetch_runs() == []
    assert store.collection_cycle(
        poller._x_collection_cycle_spec(now, _X_TOPIC_LIMIT)["collection_cycle_id"]
    ) is None
    assert coverage["periodic_requirements"] == {"x_daily": "missing"}
    assert coverage["missing_periodic_requirements"] == ["x_daily"]
    assert coverage["complete"] is False
    assert len(alerts) == 1
    store.close()


@pytest.mark.unit
def test_scheduled_period_preserves_prior_x_incident_until_x_completes(
    tmp_path, monkeypatch,
):
    first_open = _X_WINDOW_OPEN_UTC
    next_scheduled = datetime(2026, 8, 6, 0, 5, tzinfo=timezone.utc).timestamp()
    next_open = datetime(2026, 8, 6, 21, tzinfo=timezone.utc).timestamp()
    clock = {"now": first_open}
    store = SqliteMediaStore(tmp_path / "x-incident-boundary.db")
    monkeypatch.setattr(poller.time, "time", lambda: clock["now"])
    calls = []

    def unavailable_news(**_kwargs):
        calls.append(clock["now"])
        raise poller.ProviderTransientError("temporarily unavailable")

    alerts = []
    monkeypatch.setattr(poller, "fetch_top_news_headlines", unavailable_news)
    monkeypatch.setattr(
        poller,
        "emit_alert",
        lambda _component, event, **_kwargs: alerts.append(event) or True,
    )

    failed = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
    )
    clock["now"] = next_scheduled
    scheduled = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )

    assert failed["periodic_requirements"] == {"x_daily": "missing"}
    assert scheduled["periodic_requirements"] == {"x_daily": "scheduled"}
    assert scheduled["complete"] is True
    assert calls == [first_open]
    assert alerts == ["query_slot_coverage_incomplete"]
    assert store.get_meta(poller._COVERAGE_ALERT_STATE_KEY) == 1.0

    topic = {
        "topic": "trend_world",
        "category": "world",
        "query": '"Global event" reaction',
        "external_id": "headline-2",
        "title": "Global event develops - Reuters",
        "body": "summary",
        "created_utc": next_open - 10,
        "publisher": "Reuters",
        "metadata": {"publisher_domain": "reuters.com"},
    }
    monkeypatch.setattr(
        poller, "fetch_top_news_headlines", lambda **_kwargs: [topic]
    )
    monkeypatch.setattr(
        poller, "fetch_x_trends", lambda *_args, **_kwargs: [{"name": "Global event"}]
    )
    monkeypatch.setattr(
        poller, "discover_x_topics", lambda *_args, **_kwargs: [topic]
    )
    monkeypatch.setattr(poller, "fetch_x_topic", lambda *_args, **_kwargs: [])
    clock["now"] = next_open

    recovered = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
    )

    assert recovered["periodic_requirements"] == {"x_daily": "complete"}
    assert recovered["complete"] is True
    assert alerts == [
        "query_slot_coverage_incomplete",
        "query_slot_coverage_recovered",
    ]
    assert store.get_meta(poller._COVERAGE_ALERT_STATE_KEY) == 0.0
    store.close()


@pytest.mark.unit
def test_failed_open_window_attempt_stays_missing_after_start_window_closes(
    tmp_path, monkeypatch,
):
    open_now = _X_WINDOW_OPEN_UTC
    closed_now = _X_WINDOW_CLOSED_UTC
    clock = {"now": open_now}
    alerts = []
    store = SqliteMediaStore(tmp_path / "x-failed-before-midnight.db")
    monkeypatch.setattr(poller.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        poller,
        "fetch_top_news_headlines",
        lambda **_kwargs: (_ for _ in ()).throw(
            poller.ProviderTransientError("temporarily unavailable")
        ),
    )
    monkeypatch.setattr(
        poller,
        "emit_alert",
        lambda *args, **kwargs: alerts.append((args, kwargs)) or True,
    )

    first = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )
    clock["now"] = closed_now
    second = poller.run_cycle(
        store,
        tickers=[],
        sources=[],
        macro_themes={},
        x_enabled=True,
        force_x=True,
    )

    assert first["periodic_requirements"] == {"x_daily": "missing"}
    assert second["periodic_requirements"] == {"x_daily": "missing"}
    assert second["missing_periodic_requirements"] == ["x_daily"]
    assert second["complete"] is False
    assert len(alerts) == 1
    store.close()


@pytest.mark.unit
def test_discovery_queries_are_derived_only_from_ranked_headlines():
    def discover(entity, external_id):
        return poller.discover_x_topics(
            max_topics=1,
            headlines=[{
                "external_id": external_id,
                "title": f"{entity} releases a frontier AI model - Reuters",
                "body": "",
                "created_utc": 10.0,
                "publisher": "Reuters",
                "category": "technology",
                "region": "US",
                "rank": 0,
            }],
            trends=[{"name": "Unrelated entertainment trend"}],
        )[0]

    first = discover("ZephyrQuill", "first")
    second = discover("QuasarLoom", "second")

    assert first["external_id"] == "first"
    assert second["external_id"] == "second"
    assert first["query"] != second["query"]
    assert "zephyrquill" in first["query"].lower()
    assert "quasarloom" in second["query"].lower()
    assert "xtrend" not in poller.GLOBAL_EVENT_V2_PROTOCOL["evidence"]["allowed_sources"]


@pytest.mark.unit
def test_x_recent_counts_preserves_exact_window_and_decision_lineage(
    tmp_path, monkeypatch,
):
    end = datetime(2026, 8, 8, 12, tzinfo=timezone.utc).timestamp()
    start = end - 166 * 3600
    decision_id = "xdiscovery_" + "a" * 24
    decision_captured = end - 3600
    captured = {}

    def iso(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )

    bins = [
        {"start": iso(value), "end": iso(value + 3600), "tweet_count": 1}
        for value in range(int(start), int(end), 3600)
    ]

    def get_json(url, headers, timeout):
        captured.update(url=url, headers=headers, timeout=timeout)
        return {"data": bins, "meta": {"total_tweet_count": len(bins)}}

    monkeypatch.setenv("X_BEARER_TOKEN", "configured")
    monkeypatch.setattr(media_sources, "_get_json", get_json)
    rows = media_sources.fetch_x_recent_counts(
        "trend_world",
        '"Global event" reaction',
        end + 3599,
        discovery_decision_id=decision_id,
        discovery_decision_captured_utc=decision_captured,
        start_utc=start,
        end_utc=end,
    )

    params = parse_qs(urlparse(captured["url"]).query)
    assert params["query"] == [
        '("Global event" reaction) lang:en -is:retweet -is:reply'
    ]
    assert params["granularity"] == ["hour"]
    assert params["start_time"] == [iso(start)]
    assert params["end_time"] == [iso(end)]
    # The live endpoint rejects any search_count.fields value with HTTP 400
    # and already returns exactly start/end/tweet_count by default, so the
    # request must not name the fields explicitly.
    assert "search_count.fields" not in params
    snapshot = json.loads(rows[0]["body"])
    assert snapshot["discovery_decision_id"] == decision_id
    assert snapshot["discovery_decision_captured_utc"] == decision_captured
    assert snapshot["snapshot_availability"] == "terminal-fetch-receipt-only"
    assert snapshot["bin_availability"] == (
        "descriptive-components-never-independent-observations"
    )
    assert rows[0]["created_utc"] == snapshot["captured_utc"]
    assert rows[0]["metadata"]["discovery_decision_id"] == decision_id
    assert len(snapshot["bins"]) == 166

    store = SqliteMediaStore(tmp_path / "x-count-point-in-time.db")
    store.store(rows)
    assert store.history_asof(
        "2026-08-01", "2026-08-02", sources=["xcount"]
    ) == []
    assert len(store.history_asof(
        "2026-08-01", "2026-08-08", sources=["xcount"]
    )) == 1
    store.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "capture_delay", "decision_delay"),
    [
        ("q" * 512, 1, -1),
        ("valid query", 3600, -1),
        ("valid query", 1, 2),
    ],
)
def test_x_recent_counts_rejects_invalid_requests_before_provider(
    monkeypatch, query, capture_delay, decision_delay,
):
    end = datetime(2026, 8, 8, 12, tzinfo=timezone.utc).timestamp()
    start = end - 166 * 3600
    captured = end + capture_delay
    monkeypatch.setenv("X_BEARER_TOKEN", "configured")
    monkeypatch.setattr(
        media_sources,
        "_get_json",
        lambda *_args, **_kwargs: pytest.fail("invalid request reached X"),
    )
    with pytest.raises(ValueError):
        media_sources.fetch_x_recent_counts(
            "trend_world",
            query,
            captured,
            discovery_decision_id="xdiscovery_" + "b" * 24,
            discovery_decision_captured_utc=end + decision_delay,
            start_utc=start,
            end_utc=end,
        )


@pytest.mark.unit
def test_x_cost_ceiling_matches_every_declared_resource_cap():
    evidence = poller.GLOBAL_EVENT_V2_PROTOCOL["evidence"]
    billing = evidence["x_billing_accounting"]
    rates = billing["billing_rate_snapshot"]
    resources = billing["nominal_max_resources_per_day"]

    assert resources["trend_reads"] == (
        evidence["max_x_trend_requests_per_utc_day"]
        * media_sources.GLOBAL_X_ADAPTER_POLICY["trends"]["result_limit"]["default"]
    )
    assert resources["post_reads"] == (
        evidence["max_x_search_requests_per_utc_day"]
        * evidence["max_x_results_per_query"]
    )
    assert resources["expanded_user_reads"] == resources["post_reads"]
    formal_usd = (
        resources["trend_reads"] * rates["usd_per_trend_read"]
        + resources["post_reads"] * rates["usd_per_post_read"]
        + resources["expanded_user_reads"] * rates["usd_per_user_read"]
    )
    assert billing["nominal_max_usd_per_day_before_deduplication"] == (
        pytest.approx(formal_usd)
    )

    shadow_rates = X_SHADOW_POLICY["billing_rate_snapshot"]
    shadow_usd = (
        X_SHADOW_POLICY["max_trend_requests_per_utc_day"]
        * X_SHADOW_POLICY["max_trends_per_request"]
        * shadow_rates["usd_per_trend_read"]
        + X_SHADOW_POLICY["max_count_requests_per_utc_day"]
        * shadow_rates["usd_per_recent_count_request"]
    )
    assert shadow_rates["maximum_shadow_usd_per_day"] == pytest.approx(
        shadow_usd
    )
    assert shadow_rates["maximum_current_plus_shadow_usd_per_day"] == (
        pytest.approx(formal_usd + shadow_usd)
    )


@pytest.mark.unit
def test_x_shadow_is_bounded_idempotent_and_accounted_from_terminal_facts(
    tmp_path, monkeypatch,
):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-shadow.db")
    topics, decision = _complete_formal_x_cycle(
        store, monkeypatch, now, topic_count=_X_TOPIC_LIMIT
    )
    delayed = now + 5 * 3600
    clock = {"now": delayed}
    trend_calls = []
    count_calls = []
    monkeypatch.setattr(poller.time, "time", lambda: clock["now"])

    def shadow_trends(woeid, *, limit):
        trend_calls.append((woeid, limit))
        return [{"name": f"trend-{woeid}-{rank}"} for rank in range(limit)]

    def shadow_counts(topic, query, captured, **kwargs):
        count_calls.append((topic, query, captured, kwargs))
        assert kwargs["discovery_decision_id"] == decision["discovery_decision_id"]
        assert kwargs["discovery_decision_captured_utc"] == decision["captured_utc"]
        assert captured - kwargs["start_utc"] < 167 * 3600
        return [_row(
            "xcount", f"count-{len(count_calls)}", f"@{topic}", captured,
            created_utc=kwargs["end_utc"], body=query,
            metadata={"discovery_decision_id": kwargs["discovery_decision_id"]},
        )]

    monkeypatch.setattr(poller, "fetch_x_trends", shadow_trends)
    monkeypatch.setattr(poller, "fetch_x_recent_counts", shadow_counts)
    slots = poller.poll_x_shadow_once(store, now, max_topics=_X_TOPIC_LIMIT)

    spec = poller._x_shadow_collection_cycle_spec(now)
    cycle = store.collection_cycle(spec["collection_cycle_id"])
    receipts = [
        row for row in store.fetch_runs(limit=100)
        if row["collection_cycle_id"] == spec["collection_cycle_id"]
    ]
    assert cycle["status"] == "complete"
    assert trend_calls == [(23424975, 5), (23424848, 5)]
    assert len(count_calls) == len(topics) == _X_TOPIC_LIMIT
    assert len(slots) == 7
    assert {row["provider"] for row in receipts} == {"xtrend", "xcount"}
    assert all(row["cost_units"] == 1.0 for row in receipts)
    assert sum(x_shadow_receipt_usd(row) for row in receipts) == pytest.approx(0.125)
    assert sum(row["cost_units"] for row in receipts) == 7.0
    for receipt in receipts:
        metadata = json.loads(receipt["metadata_json"])
        assert metadata["protocol_id"] == X_SHADOW_PROTOCOL_ID
        assert metadata["collector_semantics_id"] == X_SHADOW_COLLECTOR_SEMANTICS_ID
        assert metadata["discovery_decision_id"] == decision["discovery_decision_id"]
        assert metadata["cost_units_semantics"] == X_SHADOW_POLICY[
            "receipt_accounting"
        ]["cost_units_semantics"]
    assert poller._x_daily_requirement_state(store, now, _X_TOPIC_LIMIT) == "complete"

    receipt_ids = {row["fetch_run_id"] for row in receipts}
    monkeypatch.setattr(
        poller, "fetch_x_trends", lambda *_args, **_kwargs: pytest.fail("retried X")
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_recent_counts",
        lambda *_args, **_kwargs: pytest.fail("retried count"),
    )
    poller.poll_x_shadow_once(store, now, max_topics=_X_TOPIC_LIMIT)
    assert receipt_ids == {
        row["fetch_run_id"] for row in store.fetch_runs(limit=100)
        if row["collection_cycle_id"] == spec["collection_cycle_id"]
    }
    store.close()


@pytest.mark.unit
def test_unknown_same_day_x_shadow_identity_skips_without_touching_formal_health(
    tmp_path, monkeypatch,
):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-shadow-old-identity.db")
    _complete_formal_x_cycle(store, monkeypatch, now)
    old = poller.media_store.collection_cycle_spec(
        cycle_kind="x-shadow-daily",
        period_key="2026-08-05",
        protocol_id="protocol_" + "1" * 24,
        collector_semantics_id="collector_" + "2" * 24,
        expected_static_slots=[("xtrend", "woeid:999")],
        max_dynamic_slots=0,
    )
    store.start_collection_cycle(old, started_utc=now)
    store.finish_collection_cycle(old["collection_cycle_id"], completed_utc=now + 1)
    before = len(store.fetch_runs(limit=100))
    monkeypatch.setattr(
        poller, "fetch_x_trends", lambda *_args, **_kwargs: pytest.fail("called X")
    )
    monkeypatch.setattr(
        poller, "fetch_x_recent_counts", lambda *_args, **_kwargs: pytest.fail("called X")
    )

    assert poller.poll_x_shadow_once(store, now, max_topics=_X_TOPIC_LIMIT) == []
    assert len(store.fetch_runs(limit=100)) == before
    assert poller._x_daily_requirement_state(store, now, _X_TOPIC_LIMIT) == "complete"
    assert store.collection_cycle(
        poller._x_shadow_collection_cycle_spec(now)["collection_cycle_id"]
    ) is None
    store.close()


@pytest.mark.unit
def test_daemon_runs_x_shadow_only_after_core_health_is_terminal(monkeypatch):
    events = []
    stop = {"flag": False}

    class Store:
        def server_observed_utc(self):
            return _X_WINDOW_OPEN_UTC

    class Health:
        def mark_cycle(self, _coverage, *, completed_utc):
            assert completed_utc > 0
            events.append("health")

    def core(*_args, **_kwargs):
        events.append("core")
        return {"periodic_requirements": {"x_daily": "complete"}}

    monkeypatch.setattr(poller, "run_cycle", core)
    monkeypatch.setattr(
        poller,
        "poll_x_shadow_once",
        lambda *_args, **_kwargs: events.append("shadow"),
    )
    monkeypatch.setattr(
        poller,
        "_sleep",
        lambda _seconds, state, **_kwargs: state.__setitem__("flag", True),
    )
    poller.poll_forever(
        Store(), [], [], 3600, {}, x_enabled=True,
        health_state=Health(), stop=stop,
        on_cycle_terminal=lambda: events.append("terminal"),
    )
    assert events == ["core", "health", "terminal", "shadow"]


@pytest.mark.unit
def test_x_shadow_provider_failures_do_not_change_formal_readiness(
    tmp_path, monkeypatch,
):
    now = _X_WINDOW_OPEN_UTC
    store = SqliteMediaStore(tmp_path / "x-shadow-provider-failure.db")
    _complete_formal_x_cycle(store, monkeypatch, now)
    monkeypatch.setattr(
        poller,
        "fetch_x_trends",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            poller.ProviderTransientError("unavailable")
        ),
    )
    monkeypatch.setattr(
        poller,
        "fetch_x_recent_counts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            poller.ProviderResponseError("bad response")
        ),
    )

    poller.poll_x_shadow_once(store, now, max_topics=_X_TOPIC_LIMIT)

    spec = poller._x_shadow_collection_cycle_spec(now)
    assert store.collection_cycle(spec["collection_cycle_id"])["status"] == "incomplete"
    assert poller._x_daily_requirement_state(store, now, _X_TOPIC_LIMIT) == "complete"
    receipts = [
        row for row in store.fetch_runs(limit=100)
        if row["collection_cycle_id"] == spec["collection_cycle_id"]
    ]
    assert len(receipts) == 3
    assert {row["status"] for row in receipts} == {"failed"}
    assert sum(x_shadow_receipt_usd(row) for row in receipts) == 0.0
    store.close()


@pytest.mark.unit
def test_one_shot_runs_x_shadow_after_formal_cycle_returns(monkeypatch):
    events = []

    class Store:
        dialect = "sqlite"

        def server_observed_utc(self):
            return _X_WINDOW_OPEN_UTC

        def close(self):
            events.append("close")

    monkeypatch.setenv("X_BEARER_TOKEN", "configured")
    monkeypatch.setattr(poller, "open_store", lambda _url: Store())
    monkeypatch.setattr(
        poller,
        "run_cycle",
        lambda *_args, **_kwargs: (
            events.append("core")
            or {"periodic_requirements": {"x_daily": "complete"}}
        ),
    )
    monkeypatch.setattr(
        poller,
        "poll_x_shadow_once",
        lambda *_args, **_kwargs: events.append("shadow"),
    )
    monkeypatch.setattr(
        poller,
        "poll_source_shadow_once",
        lambda *_args, **_kwargs: events.append("source_shadow"),
    )
    poller.main([
        "--global-only", "--once", "--sources", "x", "--no-trading-hours",
        "--interval", "3600", "--x-interval", "86400",
    ])
    assert events == ["core", "shadow", "source_shadow", "close"]
