"""GDELT DOC API backfill for historical public-news discovery evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

import requests

from tradingagents.temporal import TemporalStore, canonical_json, parse_timestamp

_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_MAX_RECORDS = 250


class GdeltResponseError(RuntimeError):
    """The public endpoint returned an error body or an unexpected payload."""


@dataclass(frozen=True)
class GdeltImportResult:
    requested: int
    imported: int
    evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]
    response_artifact_hash: str


def import_gdelt_articles(
    store: TemporalStore,
    *,
    query: str,
    start: str | datetime,
    end: str | datetime,
    max_records: int = 100,
    session: Any | None = None,
) -> GdeltImportResult:
    """Import a GDELT article-list query as transparent reconstructed evidence.

    GDELT's ``seendate`` is used as a conservative public-discovery clock, not
    as an asserted publisher timestamp. The complete returned JSON is retained
    as a raw response artifact while each article is individually searchable.
    This is discovery metadata; fetching or licensing the original publisher
    article is intentionally a separate collector decision.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= max_records <= _MAX_RECORDS:
        raise ValueError(f"max_records must be between 1 and {_MAX_RECORDS}")
    start_at = _boundary(start, is_end=False)
    end_at = _boundary(end, is_end=True)
    if start_at > end_at:
        raise ValueError("start must not be after end")
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        "startdatetime": _gdelt_timestamp(start_at),
        "enddatetime": _gdelt_timestamp(end_at),
    }
    client = session or requests.Session()
    response = client.get(_DOC_API_URL, params=params, timeout=30)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise GdeltResponseError("GDELT returned a non-JSON response (often a rate-limit message)") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise GdeltResponseError("GDELT response has no article list")
    raw_response_artifact_hash = store.put_artifact(
        canonical_json(payload).encode("utf-8"), media_type="application/json"
    )

    evidence_ids: list[str] = []
    failures: list[str] = []
    for position, article in enumerate(payload["articles"], start=1):
        if not isinstance(article, dict):
            failures.append(f"article-{position}:invalid-record")
            continue
        url = article.get("url")
        title = article.get("title")
        seen_at = _seen_at(article.get("seendate"))
        if not isinstance(url, str) or not url or not isinstance(title, str) or seen_at is None:
            failures.append(f"article-{position}:missing-url-title-or-seendate")
            continue
        record = store.record(
            "corpus.document",
            {
                "source": "gdelt-doc-2",
                "query": query,
                "url": url,
                "seendate": article["seendate"],
            },
            {
                "text": title,
                "metadata": {
                    "article": article,
                    "query": query,
                    "raw_response_artifact_hash": raw_response_artifact_hash,
                    "availability_basis": "gdelt-seendate",
                    "original_content": "not-fetched",
                },
            },
            available_at=seen_at,
            observed_at=seen_at,
            fidelity="archive-reconstructed",
            source=url,
        )
        evidence_ids.append(record.evidence_id)
    return GdeltImportResult(
        requested=len(payload["articles"]),
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
        response_artifact_hash=raw_response_artifact_hash,
    )


def _boundary(value: str | datetime, *, is_end: bool) -> datetime:
    if isinstance(value, datetime):
        return parse_timestamp(value)
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        date = datetime.fromisoformat(value).date()
        return datetime.combine(date, time.max if is_end else time.min, tzinfo=timezone.utc)
    return parse_timestamp(value)


def _gdelt_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def _seen_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.removesuffix("Z")
    for pattern in ("%Y%m%dT%H%M%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(normalized, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
