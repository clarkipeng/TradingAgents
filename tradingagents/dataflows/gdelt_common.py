"""Shared request normalization for live and archive GDELT DOC collectors."""

from __future__ import annotations

import time
from collections.abc import Mapping

GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT's DOC API allows one request every 5 seconds per client (its 429 body
# says so explicitly). One shared monotonic gate makes that an invariant of
# the process for every GDELT caller, whatever the call pattern.
GDELT_MIN_REQUEST_INTERVAL_SECONDS = 5.5
_gdelt_last_request_at = [float("-inf")]


def pace_gdelt_request() -> None:
    wait = (
        _gdelt_last_request_at[0]
        + GDELT_MIN_REQUEST_INTERVAL_SECONDS
        - time.monotonic()
    )
    if wait > 0:
        time.sleep(wait)
    _gdelt_last_request_at[0] = time.monotonic()


def normalize_gdelt_query(query: str) -> str:
    """Return one stable, non-empty GDELT query representation."""
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    normalized = " ".join(query.split())
    if not normalized:
        raise ValueError("query must not be empty")
    return normalized


def article_list_params(
    query: str,
    *,
    start: str,
    end: str,
    max_records: int,
    start_key: str = "startdatetime",
    end_key: str = "enddatetime",
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a normalized article-list request without choosing a transport."""
    params = {
        "query": normalize_gdelt_query(query),
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        start_key: start,
        end_key: end,
    }
    if extra:
        params.update(extra)
    return params
