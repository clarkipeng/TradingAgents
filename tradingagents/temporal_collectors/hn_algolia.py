"""Hacker News Algolia backfill for per-story temporal evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

import requests

from tradingagents.temporal import TemporalStore, canonical_json, parse_timestamp

_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
_MAX_RECORDS = 100


class HackerNewsArchiveResponseError(RuntimeError):
    """The public HN archive returned an unusable response."""


@dataclass(frozen=True)
class HackerNewsImportResult:
    requested: int
    imported: int
    evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]
    response_artifact_hash: str


def import_hacker_news_stories(
    store: TemporalStore,
    *,
    query: str,
    start: str | datetime,
    end: str | datetime,
    max_records: int = 100,
    session: Any | None = None,
) -> HackerNewsImportResult:
    """Import public HN stories discovered by Algolia's historical index.

    ``created_at_i`` is the HN story-publication clock. The collector keeps the
    original Algolia response as an artifact and labels every record
    ``archive-reconstructed``: a third-party search index is not a claim that
    an agent would have ranked the story the same way at the time.
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
        "tags": "story",
        "numericFilters": (
            f"created_at_i>={int(start_at.timestamp())},"
            f"created_at_i<={int(end_at.timestamp())}"
        ),
        "hitsPerPage": str(max_records),
        "page": "0",
    }
    client = session or requests.Session()
    response = client.get(_SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise HackerNewsArchiveResponseError("Hacker News archive returned non-JSON") from error
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise HackerNewsArchiveResponseError("Hacker News archive response has no story hits")

    response_artifact_hash = store.put_artifact(
        canonical_json(payload).encode("utf-8"), media_type="application/json"
    )
    evidence_ids: list[str] = []
    failures: list[str] = []
    for position, hit in enumerate(hits, start=1):
        record = _record_from_hit(
            store,
            hit,
            query=query,
            response_artifact_hash=response_artifact_hash,
        )
        if record is None:
            failures.append(f"story-{position}:invalid-record")
        else:
            evidence_ids.append(record)
    return HackerNewsImportResult(
        requested=len(hits),
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
        response_artifact_hash=response_artifact_hash,
    )


def _record_from_hit(
    store: TemporalStore,
    hit: object,
    *,
    query: str,
    response_artifact_hash: str,
) -> str | None:
    if not isinstance(hit, dict):
        return None
    object_id = hit.get("objectID")
    title = hit.get("title") or hit.get("story_title")
    created_at = hit.get("created_at_i")
    if (
        not isinstance(object_id, str)
        or not object_id
        or not isinstance(title, str)
        or not title.strip()
        or isinstance(created_at, bool)
        or not isinstance(created_at, int)
        or created_at < 0
    ):
        return None
    published_at = datetime.fromtimestamp(created_at, timezone.utc)
    discussion_url = f"https://news.ycombinator.com/item?id={object_id}"
    story_text = hit.get("story_text") or hit.get("comment_text") or ""
    if not isinstance(story_text, str):
        story_text = ""
    record = store.record(
        "corpus.document",
        {
            "source": "hacker-news-algolia",
            "external_id": object_id,
            "query": query,
        },
        {
            "text": f"{title.strip()}\n\n{story_text}".strip(),
            "metadata": {
                "story": hit,
                "query": query,
                "discussion_url": discussion_url,
                "raw_response_artifact_hash": response_artifact_hash,
                "availability_basis": "hn-created_at_i",
                "original_content": "algolia-indexed-hn-story",
            },
        },
        available_at=published_at,
        observed_at=published_at,
        event_at=published_at,
        source_published_at=published_at,
        fidelity="archive-reconstructed",
        source=discussion_url,
    )
    return record.evidence_id


def _boundary(value: str | datetime, *, is_end: bool) -> datetime:
    if isinstance(value, datetime):
        return parse_timestamp(value)
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        day = datetime.fromisoformat(value).date()
        return datetime.combine(day, time.max if is_end else time.min, tzinfo=timezone.utc)
    return parse_timestamp(value)
