"""Bounded, category-level discovery through the public GDELT DOC API."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from tradingagents.dataflows.errors import ProviderResponseError
from tradingagents.dataflows.media_sources import looks_company_authored
from tradingagents.dataflows.provider_http import get_json

_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_MAX_ARTICLES = 25
_MAX_LOOKBACK_HOURS = 48

# Subjects are deliberately broad. They contain no company, account, or security
# identifier and are only inputs to a non-formal discovery stream.
_CATEGORY_QUERIES = {
    "business_economy": "(economy OR inflation OR trade OR employment OR business)",
    "global_affairs": "(diplomacy OR election OR conflict OR sanctions OR government)",
    "science_health": "(science OR health OR climate OR energy)",
    "technology": '(technology OR "artificial intelligence" OR cybersecurity OR semiconductor)',
}
CATEGORY_QUERIES = MappingProxyType(_CATEGORY_QUERIES)

GDELT_ADAPTER_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "provider": "gdelt",
    "api": "gdelt-doc-v2",
    "endpoint": _API_URL,
    "category_queries": dict(_CATEGORY_QUERIES),
    "default_limit": 12,
    "maximum_limit": _MAX_ARTICLES,
    "default_lookback_hours": 24,
    "maximum_lookback_hours": _MAX_LOOKBACK_HOURS,
    "default_timeout_seconds": 25.0,
    "total_deadline_seconds_per_slot": 25.0,
    "maximum_attempts_per_slot": 1,
    "maximum_response_bytes": 1_000_000,
    "timestamp_semantics": "GDELT seendate is a provider observation, not publication time",
    "provider_tone_use": "ignored",
}


def fetch_gdelt_articles(
    category: str,
    fetched_at: float,
    *,
    limit: int = int(GDELT_ADAPTER_POLICY["default_limit"]),
    lookback_hours: int = int(GDELT_ADAPTER_POLICY["default_lookback_hours"]),
    timeout: float = float(GDELT_ADAPTER_POLICY["default_timeout_seconds"]),
) -> list[dict[str, Any]]:
    """Return one strict UTC-window category sample in provider-ranked order.

    ``seendate`` is retained as an observation timestamp. GDELT tone is neither
    requested nor copied into the normalized discovery record.
    """

    if category not in CATEGORY_QUERIES:
        choices = ", ".join(sorted(CATEGORY_QUERIES))
        raise ValueError(f"category must be one of: {choices}")
    _validate_positive_int("limit", limit, maximum=_MAX_ARTICLES)
    _validate_positive_int("lookback_hours", lookback_hours, maximum=_MAX_LOOKBACK_HOURS)
    received_at = _validate_timestamp(fetched_at)
    # GDELT's wire format is whole UTC seconds. Use those exact boundaries for
    # the request, response validation, and persisted manifest.
    window_end = float(math.floor(received_at))
    window_start = window_end - lookback_hours * 3600
    if window_start < 0:
        raise ValueError("lookback window must not precede the Unix epoch")

    params = {
        "ENDDATETIME": _gdelt_request_time(window_end),
        "STARTDATETIME": _gdelt_request_time(window_start),
        "format": "json",
        "maxrecords": str(limit),
        "mode": "artlist",
        "query": CATEGORY_QUERIES[category],
        "sort": "datedesc",
    }
    deadline = time.monotonic() + float(
        GDELT_ADAPTER_POLICY["total_deadline_seconds_per_slot"]
    )
    payload = get_json(
        f"{_API_URL}?{urlencode(sorted(params.items()))}",
        timeout=timeout,
        attempts=int(GDELT_ADAPTER_POLICY["maximum_attempts_per_slot"]),
        max_bytes=int(GDELT_ADAPTER_POLICY["maximum_response_bytes"]),
        deadline=deadline,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise ProviderResponseError("GDELT returned an invalid article envelope")

    rows: list[dict[str, Any]] = []
    seen_provider_items: set[str] = set()
    filtered_record_digests: list[str] = []
    duplicate_count = 0
    for rank, article in enumerate(payload["articles"], start=1):
        row = _normalize_article(
            article,
            category=category,
            rank=rank,
            window_start=window_start,
            window_end=window_end,
            fetched_at=received_at,
        )
        provider_external_id = row["metadata"]["provider_external_id"]
        if provider_external_id in seen_provider_items:
            duplicate_count += 1
            continue
        seen_provider_items.add(provider_external_id)
        if looks_company_authored(row["author"], row["title"]):
            filtered_record_digests.append(row["metadata"]["raw_lineage"]["provider_record_sha256"])
            continue
        rows.append(row)

    counts = {
        "provider_items": len(payload["articles"]),
        "filtered_company_authored": len(filtered_record_digests),
        "duplicates_collapsed": duplicate_count,
        "returned_items": len(rows),
    }
    response_sha256 = _digest(payload)
    manifest = {
        "schema_version": 1,
        "provider": "gdelt-doc-v2",
        "category": category,
        "request_window": {
            "start_utc": window_start,
            "end_utc": window_end,
        },
        "item_counts": counts,
        "returned_content_vintage_ids": [row["external_id"] for row in rows],
        "filtered_provider_record_sha256": filtered_record_digests,
        "provider_response_sha256": response_sha256,
    }
    manifest_body = _canonical_json(manifest)
    manifest_id = "gdelt_manifest_" + hashlib.sha256(manifest_body.encode("utf-8")).hexdigest()[:24]
    manifest_row = {
        "source": "gdelt",
        "external_id": manifest_id,
        "ticker": f"@GDELT_{category}".upper(),
        "subreddit": None,
        "author": None,
        "sentiment": None,
        "created_utc": None,
        "title": f"GDELT {category} discovery manifest",
        "body": manifest_body,
        "fetched_utc": received_at,
        "metadata": {
            "evidence_role": "shadow_topic_discovery_manifest",
            "provider": "gdelt-doc-v2",
            "provider_external_id": f"category:{category}",
            "content_vintage_id": manifest_id,
            "content_vintage_schema_version": 1,
            "discovery": {
                "scope": "global_news",
                "category": category,
            },
            "request_window": {
                "start_utc": window_start,
                "end_utc": window_end,
                "lookback_semantics": "explicit-inclusive-UTC-window",
            },
            "item_counts": counts,
            "raw_lineage": {
                "provider_response_sha256": response_sha256,
            },
        },
    }
    return [manifest_row, *rows]


def _normalize_article(
    article: object,
    *,
    category: str,
    rank: int,
    window_start: float,
    window_end: float,
    fetched_at: float,
) -> dict[str, Any]:
    if not isinstance(article, dict):
        raise ProviderResponseError("GDELT returned an invalid article")

    title = _required_text(article, "title")
    article_url, publisher_domain = _public_article_url(_required_text(article, "url"))
    provider_domain = _optional_text(article, "domain")
    reported_domain = _normalize_domain(provider_domain) if provider_domain else None
    language = _required_text(article, "language")
    country = _required_text(article, "sourcecountry")
    seen_at_raw = _required_text(article, "seendate")
    seen_at = _parse_gdelt_time(seen_at_raw)
    if not window_start <= seen_at <= window_end:
        raise ProviderResponseError("GDELT article lies outside the requested UTC window")

    record_sha256 = _digest(article)
    snapshot = {
        "schema_version": 1,
        "provider": "gdelt-doc-v2",
        "category": category,
        "provider_rank": rank,
        "provider_external_id": article_url,
        "title": title,
        "article_url": article_url,
        "publisher_domain": publisher_domain,
        "provider_reported_domain": reported_domain,
        "language": language,
        "source_country": country,
        "source_observed_utc": seen_at,
        "provider_record_sha256": record_sha256,
    }
    body = _canonical_json(snapshot)
    external_id = f"gdelt_{hashlib.sha256(body.encode('utf-8')).hexdigest()[:24]}"

    return {
        "source": "gdelt",
        "external_id": external_id,
        "ticker": f"@GDELT_{category}".upper(),
        "subreddit": None,
        "author": publisher_domain,
        "sentiment": None,
        "created_utc": seen_at,
        "title": title,
        "body": body,
        "fetched_utc": fetched_at,
        "metadata": {
            "evidence_role": "shadow_topic_discovery",
            "provider": "gdelt-doc-v2",
            "provider_external_id": article_url,
            "content_vintage_id": external_id,
            "content_vintage_schema_version": 1,
            "article_url": article_url,
            "publisher_domain": publisher_domain,
            "provider_reported_domain": reported_domain,
            "language": language,
            "source_country": country,
            "discovery": {
                "scope": "global_news",
                "category": category,
                "provider_rank": rank,
            },
            "source_observed_utc": seen_at,
            "provider_seen_at_raw": seen_at_raw,
            "timestamp_semantics": GDELT_ADAPTER_POLICY["timestamp_semantics"],
            "request_window": {
                "start_utc": window_start,
                "end_utc": window_end,
                "lookback_semantics": "explicit-inclusive-UTC-window",
            },
            "raw_lineage": {
                "provider_record_sha256": record_sha256,
            },
        },
    }


def _required_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError(f"GDELT article has invalid {field}")
    return value.strip()


def _optional_text(row: dict[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError(f"GDELT article has invalid {field}")
    return value.strip()


def _public_article_url(raw_url: str) -> tuple[str, str]:
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ProviderResponseError("GDELT article has an invalid public URL")
    domain = _normalize_domain(parsed.hostname)
    path = parsed.path or "/"
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, "")
    ), domain


def _normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or any(character.isspace() for character in domain):
        raise ProviderResponseError("GDELT article has an invalid publisher domain")
    return domain


def _parse_gdelt_time(value: str) -> float:
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            parsed = datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderResponseError("GDELT article has an invalid seendate") from None
    if parsed.tzinfo is None:
        raise ProviderResponseError("GDELT article seendate lacks a timezone")
    return parsed.timestamp()


def _gdelt_request_time(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y%m%d%H%M%S")


def _validate_positive_int(name: str, value: int, *, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")


def _validate_timestamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("fetched_at must be a non-negative finite timestamp")
    timestamp = float(value)
    if timestamp < 0 or not math.isfinite(timestamp):
        raise ValueError("fetched_at must be a non-negative finite timestamp")
    return timestamp


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
