"""Versioned fixed roster of daily X cashtag captures.

The roster closes the X query universe: every subject an agent may ask the
X tool about is captured every UTC day, so replay coverage is total by
construction - the same guarantee per-ticker news and StockTwits already
have. Changing the roster is a versioned identity change: new entries have
no capture history before the day they join, and replay says so instead of
pretending.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from tradingagents.dataflows import media_store


def _content_id(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(encoded).hexdigest()[:24]}"


# Fifty liquid US names spanning sectors; sector coverage arrives through the
# largest stocks in each sector rather than vague sector-text searches, which
# X handles poorly. Frozen: edits change the roster identity.
X_ROSTER_TICKERS = (
    "AAPL", "AMZN", "AVGO", "BRK.B", "COIN", "COST", "CRM", "GOOGL", "JPM",
    "LLY", "MA", "META", "MSFT", "NFLX", "NVDA", "ORCL", "PLTR", "TSLA",
    "UNH", "V", "XOM", "AMD", "AMAT", "BAC", "GS", "INTC", "MCD", "MS",
    "QQQ", "SPY",
    "TSM", "WMT", "HD", "PG", "JNJ", "ABBV", "MRK", "PEP", "KO", "CVX",
    "WFC", "C", "T", "VZ", "DIS", "NKE", "BA", "CAT", "GE", "IBM",
)

X_ROSTER_STATIC_SLOTS = tuple(
    ("x", f"cashtag:{ticker}") for ticker in X_ROSTER_TICKERS
)

X_ROSTER_V1_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "name": "x-roster-daily-v1",
    "evidence_role": "unverified_public_reaction",
    "formal_projection_allowed": True,
    "cycle_kind": "x-roster-daily",
    "period_timezone": "UTC",
    "cadence": "once-per-UTC-day",
    "static_slots": [
        {"provider": provider, "query_key": query_key}
        for provider, query_key in X_ROSTER_STATIC_SLOTS
    ],
    "maximum_dynamic_slots": 0,
    "results_per_query": 10,
    # Relevancy ranks the day's prominent posts; recency would sample noise.
    "sort_order": "relevancy",
    "query_shape": "cashtag-with-standard-public-reaction-filters",
    "max_requests_per_utc_day": len(X_ROSTER_STATIC_SLOTS),
    "recovery_stale_seconds": 300.0,
}

X_ROSTER_V1_PROTOCOL_MANIFEST = {
    "schema_version": 1,
    "policy": X_ROSTER_V1_POLICY,
    "permitted_use": (
        "formal public-reaction evidence; the closed query universe an agent"
        " X tool may address in every mode"
    ),
    "prohibited_uses": ["sentiment ground truth without corroboration"],
}
X_ROSTER_V1_PROTOCOL_ID = _content_id(
    X_ROSTER_V1_PROTOCOL_MANIFEST, prefix="protocol_"
)

X_ROSTER_V1_COLLECTOR_SEMANTICS_MANIFEST = {
    "schema_version": 1,
    "policy_id": _content_id(X_ROSTER_V1_POLICY, prefix="x_roster_policy_"),
    "author_screening": "same recent-search screening as the global X adapter",
    "timestamp_semantics": (
        "post created_utc is source publication; availability requires the"
        " server-terminal fetch receipt"
    ),
    "formal_projection_allowed": True,
}
X_ROSTER_V1_COLLECTOR_SEMANTICS_ID = _content_id(
    X_ROSTER_V1_COLLECTOR_SEMANTICS_MANIFEST, prefix="collector_"
)


class XRosterCycleIdentityError(RuntimeError):
    """A same-day roster attempt has an unrecognized identity."""


def _validated_timestamp(now: float) -> float:
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("X roster cycle time must be finite")
    value = float(now)
    if not (value == value and value not in (float("inf"), float("-inf"))):
        raise ValueError("X roster cycle time must be finite")
    return value


def x_roster_cycle_spec(now: float) -> dict:
    """Return the immutable identity for one UTC day's roster cycle."""
    timestamp = _validated_timestamp(now)
    period_key = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
    return media_store.collection_cycle_spec(
        cycle_kind=X_ROSTER_V1_POLICY["cycle_kind"],
        period_key=period_key,
        protocol_id=X_ROSTER_V1_PROTOCOL_ID,
        collector_semantics_id=X_ROSTER_V1_COLLECTOR_SEMANTICS_ID,
        expected_static_slots=list(X_ROSTER_STATIC_SLOTS),
        max_dynamic_slots=0,
    )


def x_roster_cycle_resolution(store: Any, now: float) -> dict:
    """Distinguish a callable current identity from a prior same-day attempt."""
    spec = x_roster_cycle_spec(now)
    identity = spec["identity"]
    observed = store.collection_cycle_identities(
        identity["cycle_kind"], period_key=identity["period_key"]
    )
    identity_fields = {"collection_cycle_id", "protocol_id", "collector_semantics_id"}
    if not isinstance(observed, list) or any(
        not isinstance(row, dict)
        or set(row) != identity_fields
        or any(not isinstance(row[field], str) or not row[field] for field in identity_fields)
        for row in observed
    ):
        raise ValueError("X roster cycle identity inventory is malformed")
    expected = {
        "collection_cycle_id": spec["collection_cycle_id"],
        "protocol_id": identity["protocol_id"],
        "collector_semantics_id": identity["collector_semantics_id"],
    }
    if any(row != expected for row in observed):
        return {"state": "other_identity_already_attempted", "spec": None}
    return {"state": "ready", "spec": spec}
