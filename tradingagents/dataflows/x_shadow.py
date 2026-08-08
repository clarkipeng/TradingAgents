"""Isolated contracts for non-formal X discovery telemetry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from tradingagents.dataflows.media_sources import (
    global_x_adapter_policy_manifest,
    global_x_shadow_adapter_policy_manifest,
)

X_SHADOW_POLICY = MappingProxyType({
    "schema_version": 1,
    "name": "global-x-shadow-v1",
    "cycle_kind": "x-shadow-daily",
    "topic_query_source": "stored-formal-x-discovery-decision-v1",
    "trend_woeids": (23424975, 23424848),  # United Kingdom, India
    "max_trend_requests_per_utc_day": 2,
    "max_trends_per_request": 5,
    "max_count_requests_per_utc_day": 5,
    "count_window_anchor": "shadow-request-completed-hour-floor-v1",
    "restart_policy": "one-terminal-attempt-per-utc-day-v1",
    "billing_rate_snapshot": MappingProxyType({
        "observed_utc_date": "2026-08-08",
        "official_source": "https://docs.x.com/x-api/getting-started/pricing",
        "usd_per_post_read": 0.005,
        "usd_per_user_read": 0.010,
        "usd_per_trend_read": 0.010,
        "usd_per_recent_count_request": 0.005,
        "daily_deduplication": "provider soft guarantee within UTC day",
        "maximum_shadow_usd_per_day": 0.125,
        "maximum_current_plus_shadow_usd_per_day": 1.475,
    }),
    "receipt_accounting": MappingProxyType({
        "cost_units_semantics": "durable-request-budget-reservation-unit-not-usd",
        "regional_trend": MappingProxyType({
            "provider": "xtrend",
            "billable_quantity": "terminal-item-count-returned-resources",
            "usd_per_item": 0.010,
        }),
        "recent_count": MappingProxyType({
            "provider": "xcount",
            "billable_quantity": "successful-terminal-request",
            "usd_per_request": 0.005,
        }),
    }),
})


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _content_id(value: Any, prefix: str) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


X_SHADOW_STATIC_SLOTS = tuple(
    ("xtrend", f"woeid:{int(woeid)}")
    for woeid in X_SHADOW_POLICY["trend_woeids"]
)

_formal_adapter = global_x_adapter_policy_manifest()
_shadow_adapter = global_x_shadow_adapter_policy_manifest()
X_SHADOW_PROTOCOL_MANIFEST = {
    "schema_version": 1,
    "name": X_SHADOW_POLICY["name"],
    "cycle_kind": X_SHADOW_POLICY["cycle_kind"],
    "formal_effect": "none",
    "inputs": {
        "topic_queries": X_SHADOW_POLICY["topic_query_source"],
        "trend_woeids": list(X_SHADOW_POLICY["trend_woeids"]),
    },
    "requests": {
        "trends": _formal_adapter["trends"],
        "recent_counts": _shadow_adapter["recent_counts"],
        "max_trend_requests_per_utc_day": X_SHADOW_POLICY[
            "max_trend_requests_per_utc_day"
        ],
        "max_trends_per_request": X_SHADOW_POLICY["max_trends_per_request"],
        "max_count_requests_per_utc_day": X_SHADOW_POLICY[
            "max_count_requests_per_utc_day"
        ],
    },
    "cycle": {
        "static_slots": [list(slot) for slot in X_SHADOW_STATIC_SLOTS],
        "max_dynamic_slots": X_SHADOW_POLICY["max_count_requests_per_utc_day"],
        "restart_policy": X_SHADOW_POLICY["restart_policy"],
    },
    "billing_rate_snapshot": _plain(X_SHADOW_POLICY["billing_rate_snapshot"]),
    "receipt_accounting": _plain(X_SHADOW_POLICY["receipt_accounting"]),
}
X_SHADOW_PROTOCOL_ID = _content_id(X_SHADOW_PROTOCOL_MANIFEST, "protocol_")

X_SHADOW_SEMANTICS_MANIFEST = {
    "schema_version": 1,
    "cycle_kind": X_SHADOW_POLICY["cycle_kind"],
    "formal_projection": False,
    "sources": {
        "xtrend": "ranked-trend-discovery-only-v1",
        "xcount": "hourly-public-attention-shadow-v1",
    },
    "count_window": {
        "anchor": X_SHADOW_POLICY["count_window_anchor"],
        "lookback_seconds": _shadow_adapter["recent_counts"]["lookback_seconds"],
        "granularity": _shadow_adapter["recent_counts"]["granularity"],
    },
    "point_in_time_availability": {
        "unit": "whole-xcount-snapshot",
        "available_at": _shadow_adapter["recent_counts"]["snapshot_availability"],
        "retrospective_bins": _shadow_adapter["recent_counts"]["bin_availability"],
        "selection_time_field": "discovery_decision_captured_utc",
        "row_created_time_field": "captured_utc",
    },
    "wire_formats": {
        "collection_cycle_identity": 1,
        "collection_cycle_manifest": 2,
        "xcount_snapshot": 1,
    },
}
X_SHADOW_COLLECTOR_SEMANTICS_ID = _content_id(
    X_SHADOW_SEMANTICS_MANIFEST, "collector_"
)


def x_shadow_receipt_usd(receipt: Mapping) -> float:
    """Derive estimated provider USD from terminal facts, never ``cost_units``."""
    if not isinstance(receipt, Mapping):
        raise ValueError("X shadow receipt must be a mapping")
    metadata = receipt.get("metadata_json")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise ValueError("X shadow receipt metadata is invalid") from exc
    if not isinstance(metadata, Mapping) or metadata.get(
        "cost_units_semantics"
    ) != X_SHADOW_POLICY["receipt_accounting"]["cost_units_semantics"]:
        raise ValueError("X shadow receipt accounting contract is missing")

    provider = receipt.get("provider")
    status = receipt.get("status")
    item_count = receipt.get("item_count")
    if status not in {"success", "empty", "failed"}:
        raise ValueError("X shadow receipt must be terminal")
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        raise ValueError("X shadow receipt item count is invalid")
    if provider == "xtrend":
        maximum = int(X_SHADOW_POLICY["max_trends_per_request"])
        if item_count > maximum:
            raise ValueError("X shadow trend receipt exceeds its resource cap")
        return item_count * float(
            X_SHADOW_POLICY["receipt_accounting"]["regional_trend"]["usd_per_item"]
        )
    if provider == "xcount":
        if status == "success" and item_count != 1:
            raise ValueError("successful X count receipts contain one snapshot")
        if status != "success" and item_count != 0:
            raise ValueError("non-success X count receipts cannot contain a snapshot")
        return float(
            X_SHADOW_POLICY["receipt_accounting"]["recent_count"][
                "usd_per_request"
            ]
        ) if status == "success" else 0.0
    raise ValueError("receipt is not an X shadow provider")
