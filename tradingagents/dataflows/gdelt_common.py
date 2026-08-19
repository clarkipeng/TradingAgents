"""Shared request normalization for live and archive GDELT DOC collectors."""

from __future__ import annotations

from collections.abc import Mapping

GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


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
