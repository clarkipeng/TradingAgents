"""Bounded technology-topic discovery from Hacker News' public top feed."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from tradingagents.dataflows.errors import ProviderResponseError
from tradingagents.dataflows.media_sources import looks_company_authored
from tradingagents.dataflows.provider_http import get_json

_API_URL = "https://hacker-news.firebaseio.com/v0"
_MAX_STORIES = 12
_MAX_FEED_ITEMS = 500

HACKER_NEWS_ADAPTER_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "provider": "hacker_news",
    "api": "hacker-news-api-v0",
    "endpoint": _API_URL,
    "feed": "top",
    "feed_endpoint": "topstories",
    "default_limit": 8,
    "maximum_limit": _MAX_STORIES,
    "maximum_feed_items": _MAX_FEED_ITEMS,
    "default_timeout_seconds": 3.0,
    "total_deadline_seconds": 45.0,
    "maximum_attempts_per_request": 2,
    "maximum_feed_bytes": 64_000,
    "maximum_item_bytes": 256_000,
    "sample_semantics": "provider-ordered technology-community discovery",
}


def fetch_hacker_news_stories(
    feed: str,
    fetched_at: float,
    *,
    limit: int = int(HACKER_NEWS_ADAPTER_POLICY["default_limit"]),
    timeout: float = float(HACKER_NEWS_ADAPTER_POLICY["default_timeout_seconds"]),
    deadline_seconds: float = float(HACKER_NEWS_ADAPTER_POLICY["total_deadline_seconds"]),
) -> list[dict[str, Any]]:
    """Return a content-bound top-feed manifest followed by valid story rows.

    The manifest retains the provider's complete ordered ID list and the exact
    sampled rank outcomes, including missing, deleted, dead, and non-story
    entries. Mutable rank and engagement values are bound into each story body.
    """

    if feed != HACKER_NEWS_ADAPTER_POLICY["feed"]:
        raise ValueError("feed must be: top")
    _validate_limit(limit)
    received_at = _validate_timestamp(fetched_at)
    timeout = _validate_duration("timeout", timeout)
    deadline_seconds = _validate_duration("deadline_seconds", deadline_seconds)
    deadline = time.monotonic() + deadline_seconds
    request_options = {
        "timeout": timeout,
        "attempts": int(HACKER_NEWS_ADAPTER_POLICY["maximum_attempts_per_request"]),
        "deadline": deadline,
    }

    item_ids = get_json(
        f"{_API_URL}/{HACKER_NEWS_ADAPTER_POLICY['feed_endpoint']}.json",
        max_bytes=int(HACKER_NEWS_ADAPTER_POLICY["maximum_feed_bytes"]),
        **request_options,
    )
    ordered_ids = _validated_feed(item_ids)

    outcomes: list[dict[str, Any]] = []
    stories: list[dict[str, Any]] = []
    for rank, item_id in enumerate(ordered_ids[:limit], start=1):
        item = get_json(
            f"{_API_URL}/item/{item_id}.json",
            max_bytes=int(HACKER_NEWS_ADAPTER_POLICY["maximum_item_bytes"]),
            **request_options,
        )
        outcome, row = _normalize_feed_item(
            item,
            expected_id=item_id,
            feed=feed,
            rank=rank,
            fetched_at=received_at,
        )
        outcomes.append(outcome)
        if row is not None:
            stories.append(row)

    manifest = {
        "schema_version": 1,
        "provider": "hacker-news-api-v0",
        "feed": feed,
        "ordered_item_ids": ordered_ids,
        "sample_limit": limit,
        "sampled_items": outcomes,
    }
    status_counts = {
        status: sum(outcome["status"] == status for outcome in outcomes)
        for status in (
            "story",
            "missing",
            "deleted",
            "deleted_dead",
            "dead",
            "non_story",
        )
    }
    item_counts = {
        "provider_feed_items": len(ordered_ids),
        "sampled_items": len(outcomes),
        "returned_items": len(stories),
        **status_counts,
    }
    manifest["item_counts"] = item_counts
    manifest_body = _canonical_json(manifest)
    manifest_id = "hnfeed_" + hashlib.sha256(manifest_body.encode("utf-8")).hexdigest()[:24]
    manifest_row = {
        "source": "hacker_news",
        "external_id": manifest_id,
        "ticker": "@HACKER_NEWS_TECHNOLOGY",
        "subreddit": None,
        "author": None,
        "sentiment": None,
        "created_utc": None,
        "title": "Hacker News top feed manifest",
        "body": manifest_body,
        "fetched_utc": received_at,
        "metadata": {
            "evidence_role": "shadow_topic_discovery_manifest",
            "provider": "hacker-news-api-v0",
            "provider_external_id": "topstories",
            "content_vintage_id": manifest_id,
            "content_vintage_schema_version": 1,
            "discovery": {
                "scope": "technology_community",
                "feed": feed,
                "sample_semantics": HACKER_NEWS_ADAPTER_POLICY["sample_semantics"],
            },
            "feed_item_count": len(ordered_ids),
            "sample_limit": limit,
            "item_counts": item_counts,
            "raw_lineage": {
                "provider_feed_sha256": _digest(ordered_ids),
            },
        },
    }
    return [manifest_row, *stories]


def _normalize_feed_item(
    item: object,
    *,
    expected_id: int,
    feed: str,
    rank: int,
    fetched_at: float,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if item is None:
        return {
            "rank": rank,
            "item_id": expected_id,
            "status": "missing",
            "provider_record_sha256": None,
        }, None
    if not isinstance(item, dict):
        raise ProviderResponseError("Hacker News returned an invalid item")

    item_id = _non_negative_int(item, "id", positive=True)
    if item_id != expected_id:
        raise ProviderResponseError("Hacker News item id does not match the requested id")
    for flag in ("deleted", "dead"):
        if flag in item and not isinstance(item[flag], bool):
            raise ProviderResponseError(f"Hacker News item has invalid {flag}")

    record_sha256 = _digest(item)
    deleted = item.get("deleted") is True
    dead = item.get("dead") is True
    item_type = item.get("type")
    if item_type is not None and not isinstance(item_type, str):
        raise ProviderResponseError("Hacker News item has invalid type")
    if deleted or dead or item_type != "story":
        status = (
            "deleted_dead"
            if deleted and dead
            else "deleted"
            if deleted
            else "dead"
            if dead
            else "non_story"
        )
        return {
            "rank": rank,
            "item_id": expected_id,
            "status": status,
            "item_type": item_type,
            "provider_record_sha256": record_sha256,
        }, None

    row = _normalize_story(
        item,
        expected_id=expected_id,
        feed=feed,
        rank=rank,
        fetched_at=fetched_at,
        record_sha256=record_sha256,
    )
    return {
        "rank": rank,
        "item_id": expected_id,
        "status": "story",
        "content_vintage_id": row["external_id"],
        "provider_record_sha256": record_sha256,
    }, row


def _normalize_story(
    item: dict[str, Any],
    *,
    expected_id: int,
    feed: str,
    rank: int,
    fetched_at: float,
    record_sha256: str,
) -> dict[str, Any]:
    title = _required_text(item, "title")
    author = _required_text(item, "by")
    published_at = float(_non_negative_int(item, "time", positive=True))
    score = _non_negative_int(item, "score")
    comment_count = _non_negative_int(item, "descendants", default=0)
    discussion_url = f"https://news.ycombinator.com/item?id={expected_id}"
    outbound_url, outbound_domain = _optional_public_url(item.get("url"))
    story_text = _optional_text(item, "text")
    article_url = outbound_url or discussion_url
    publisher_domain = outbound_domain or "news.ycombinator.com"
    outbound_looks_company_authored = bool(
        outbound_domain and looks_company_authored(outbound_domain, title)
    )
    engagement = {
        "feed": feed,
        "rank": rank,
        "score": score,
        "comment_count": comment_count,
    }
    snapshot = {
        "schema_version": 1,
        "provider": "hacker-news-api-v0",
        "provider_external_id": str(expected_id),
        "feed": feed,
        "rank": rank,
        "title": title,
        "author": author,
        "source_published_utc": published_at,
        "article_url": article_url,
        "outbound_url": outbound_url,
        "discussion_url": discussion_url,
        "story_text": story_text,
        "outbound_looks_company_authored": outbound_looks_company_authored,
        "engagement": engagement,
        "provider_record_sha256": record_sha256,
    }
    body = _canonical_json(snapshot)
    external_id = f"hn_{expected_id}_{hashlib.sha256(body.encode('utf-8')).hexdigest()[:24]}"

    return {
        "source": "hacker_news",
        "external_id": external_id,
        "ticker": "@HACKER_NEWS_TECHNOLOGY",
        "subreddit": None,
        "author": author,
        "sentiment": None,
        "created_utc": published_at,
        "title": title,
        "body": body,
        "fetched_utc": fetched_at,
        "metadata": {
            "evidence_role": "shadow_topic_discovery",
            "provider": "hacker-news-api-v0",
            "provider_external_id": str(expected_id),
            "content_vintage_id": external_id,
            "content_vintage_schema_version": 1,
            "article_url": article_url,
            "publisher_domain": publisher_domain,
            "outbound_url": outbound_url,
            "discussion_url": discussion_url,
            "source_item_type": "story",
            "outbound_looks_company_authored": outbound_looks_company_authored,
            "discovery": {
                "scope": "technology_community",
                "feed": feed,
                "provider_rank": rank,
                "sample_semantics": HACKER_NEWS_ADAPTER_POLICY["sample_semantics"],
            },
            "engagement": engagement,
            "raw_lineage": {
                "provider_record_sha256": record_sha256,
            },
        },
    }


def _validated_feed(value: object) -> list[int]:
    if not isinstance(value, list) or not value or len(value) > _MAX_FEED_ITEMS:
        raise ProviderResponseError("Hacker News returned an invalid top-feed manifest")
    if any(
        isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0
        for item_id in value
    ):
        raise ProviderResponseError("Hacker News feed contains an invalid item id")
    if len(set(value)) != len(value):
        raise ProviderResponseError("Hacker News feed contains duplicate item ids")
    return list(value)


def _required_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError(f"Hacker News item has invalid {field}")
    return value.strip()


def _optional_text(row: dict[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderResponseError(f"Hacker News item has invalid {field}")
    return value


def _non_negative_int(
    row: dict[str, Any],
    field: str,
    *,
    default: int | None = None,
    positive: bool = False,
) -> int:
    value = row.get(field, default)
    lower_bound = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < lower_bound:
        raise ProviderResponseError(f"Hacker News item has invalid {field}")
    return value


def _optional_public_url(value: object) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError("Hacker News item has invalid url")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ProviderResponseError("Hacker News item has invalid public URL")
    path = parsed.path or "/"
    normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))
    domain = parsed.hostname.lower().removeprefix("www.").rstrip(".")
    return normalized, domain


def _validate_limit(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_STORIES:
        raise ValueError(f"limit must be between 1 and {_MAX_STORIES}")


def _validate_timestamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("fetched_at must be a non-negative finite timestamp")
    timestamp = float(value)
    if timestamp < 0 or not math.isfinite(timestamp):
        raise ValueError("fetched_at must be a non-negative finite timestamp")
    return timestamp


def _validate_duration(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive finite duration")
    duration = float(value)
    if duration <= 0 or not math.isfinite(duration):
        raise ValueError(f"{name} must be a positive finite duration")
    return duration


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
