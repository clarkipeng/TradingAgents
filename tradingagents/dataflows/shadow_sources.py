"""Versioned, non-formal contracts for bounded public topic discovery."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from tradingagents.dataflows import gdelt, hacker_news, media_store


def _content_id(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(encoded).hexdigest()[:24]}"


SOURCE_SHADOW_STATIC_SLOTS = (
    ("gdelt", "category:business_economy"),
    ("gdelt", "category:global_affairs"),
    ("gdelt", "category:science_health"),
    ("gdelt", "category:technology"),
    ("hacker_news", "feed:top"),
)

SOURCE_SHADOW_V1_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "name": "public-source-shadow-v1",
    "evidence_role": "shadow_topic_discovery_only",
    "formal_projection_allowed": False,
    "cycle_kind": "source-shadow-daily",
    "period_timezone": "UTC",
    "cadence": "once-per-UTC-day",
    "static_slots": [
        {"provider": provider, "query_key": query_key}
        for provider, query_key in SOURCE_SHADOW_STATIC_SLOTS
    ],
    "maximum_dynamic_slots": 0,
    "maximum_sequential_runtime_seconds": 90.0,
    "recovery_stale_seconds": 300.0,
    "gdelt_company_authorship_filter": "media_sources.looks_company_authored",
    "hacker_news_first_party_handling": (
        "retain community-ranked story and label outbound authorship"
    ),
    "adapters": {
        "gdelt": gdelt.GDELT_ADAPTER_POLICY,
        "hacker_news": hacker_news.HACKER_NEWS_ADAPTER_POLICY,
    },
}

SOURCE_SHADOW_V1_PROTOCOL_MANIFEST = {
    "schema_version": 1,
    "policy": SOURCE_SHADOW_V1_POLICY,
    "sampling": {
        "gdelt": "four broad category requests with explicit UTC windows",
        "hacker_news": "one bounded top-feed sample",
    },
    "permitted_use": "future topic-discovery research after independent corroboration",
    "prohibited_uses": [
        "formal forecast input",
        "formal availability input",
        "sentiment ground truth",
        "company-authored evidence",
    ],
}
SOURCE_SHADOW_V1_PROTOCOL_ID = _content_id(
    SOURCE_SHADOW_V1_PROTOCOL_MANIFEST,
    prefix="protocol_",
)

SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_MANIFEST = {
    "schema_version": 1,
    "policy_id": _content_id(SOURCE_SHADOW_V1_POLICY, prefix="shadow_policy_"),
    "media_row_shape": [
        "source",
        "external_id",
        "ticker",
        "subreddit",
        "author",
        "sentiment",
        "created_utc",
        "title",
        "body",
        "fetched_utc",
        "metadata",
    ],
    "external_identity": "sha256-normalized-content-vintage-v1",
    "mutable_snapshot_binding": {
        "gdelt": "category, provider rank, article projection, and seendate in body",
        "hacker_news": "feed rank, score, comments, and story projection in body",
    },
    "manifest_semantics": {
        "gdelt": "received, company-filtered, duplicate, and returned counts",
        "hacker_news": "complete ordered feed IDs and exact sampled item outcomes",
    },
    "timestamp_semantics": {
        "availability": (
            "all shadow rows require their server-terminal fetch receipt; "
            "created_utc alone never establishes availability"
        ),
        "gdelt": gdelt.GDELT_ADAPTER_POLICY["timestamp_semantics"],
        "hacker_news": (
            "item time is source publication; feed rank, score, and comments "
            "are capture-time measurements available only at the fetch receipt"
        ),
    },
    "formal_projection_allowed": False,
}
SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID = _content_id(
    SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_MANIFEST,
    prefix="collector_",
)


class SourceShadowCycleIdentityError(RuntimeError):
    """A same-day source-shadow attempt has an unrecognized identity."""


def source_shadow_cycle_spec(now: float) -> dict:
    """Return the immutable identity for one UTC day's source-shadow cycle."""

    timestamp = _validated_timestamp(now)
    period_key = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
    return media_store.collection_cycle_spec(
        cycle_kind=SOURCE_SHADOW_V1_POLICY["cycle_kind"],
        period_key=period_key,
        protocol_id=SOURCE_SHADOW_V1_PROTOCOL_ID,
        collector_semantics_id=SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID,
        expected_static_slots=list(SOURCE_SHADOW_STATIC_SLOTS),
        max_dynamic_slots=0,
    )


def checked_source_shadow_cycle_spec(store: Any, now: float) -> dict:
    """Return today's spec only when no incompatible attempt already exists."""

    resolution = source_shadow_cycle_resolution(store, now)
    if resolution["state"] != "ready":
        raise SourceShadowCycleIdentityError(
            "a different source-shadow cycle already exists for this UTC day"
        )
    return resolution["spec"]


def source_shadow_cycle_resolution(store: Any, now: float) -> dict:
    """Distinguish a callable current identity from a prior same-day attempt."""

    spec = source_shadow_cycle_spec(now)
    identity = spec["identity"]
    observed = store.collection_cycle_identities(
        identity["cycle_kind"],
        period_key=identity["period_key"],
    )
    identity_fields = {
        "collection_cycle_id",
        "protocol_id",
        "collector_semantics_id",
    }
    if not isinstance(observed, list) or any(
        not isinstance(row, dict)
        or set(row) != identity_fields
        or any(not isinstance(row[field], str) or not row[field] for field in identity_fields)
        for row in observed
    ):
        raise ValueError("source-shadow cycle identity inventory is malformed")
    expected = {
        "collection_cycle_id": spec["collection_cycle_id"],
        "protocol_id": identity["protocol_id"],
        "collector_semantics_id": identity["collector_semantics_id"],
    }
    if any(row != expected for row in observed):
        return {"state": "other_identity_already_attempted", "spec": None}
    return {"state": "ready", "spec": spec}


def fetch_source_shadow_slot(
    provider: str,
    query_key: str,
    fetched_at: float,
) -> list[dict[str, Any]]:
    """Dispatch one declared slot without accepting arbitrary provider queries."""

    slot = (provider, query_key)
    if slot not in SOURCE_SHADOW_STATIC_SLOTS:
        raise ValueError("source-shadow slot is not declared by the active policy")
    if provider == "gdelt":
        category = query_key.removeprefix("category:")
        return gdelt.fetch_gdelt_articles(category, fetched_at)
    if provider == "hacker_news":
        return hacker_news.fetch_hacker_news_stories("top", fetched_at)
    raise AssertionError("unreachable")


def _validated_timestamp(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError("now must be a non-negative finite timestamp")
    return float(value)


__all__ = [
    "SOURCE_SHADOW_STATIC_SLOTS",
    "SOURCE_SHADOW_V1_POLICY",
    "SOURCE_SHADOW_V1_PROTOCOL_ID",
    "SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID",
    "SourceShadowCycleIdentityError",
    "checked_source_shadow_cycle_spec",
    "fetch_source_shadow_slot",
    "source_shadow_cycle_resolution",
    "source_shadow_cycle_spec",
]
