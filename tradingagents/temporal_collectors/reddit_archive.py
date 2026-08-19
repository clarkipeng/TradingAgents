"""Bounded Arctic Shift Reddit backfill for temporal social documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

import requests

from tradingagents.temporal import TemporalStore, canonical_json, parse_timestamp

_API_BASE = "https://arctic-shift.photon-reddit.com/api"
_MAX_RECORDS = 1_000
_MAX_PER_REQUEST = 100


class RedditArchiveResponseError(RuntimeError):
    """The archive returned a response that cannot be treated as public posts."""


@dataclass(frozen=True)
class RedditArchiveImportResult:
    requested: int
    imported: int
    evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]
    response_artifact_hashes: tuple[str, ...]


def import_reddit_archive(
    store: TemporalStore,
    *,
    ticker: str,
    start: str | datetime,
    end: str | datetime,
    subreddits: tuple[str, ...] = ("wallstreetbets", "stocks", "investing"),
    max_records: int = 100,
    session: Any | None = None,
) -> RedditArchiveImportResult:
    """Backfill public posts and comments mentioning ``ticker``.

    Arctic Shift's post ``query`` and comment ``body`` search are restricted to
    declared subreddits and a date range. The archive's source timestamp becomes
    the reconstructed availability clock; each raw API response is retained so
    later evaluation can distinguish source evidence from today’s retrieval.
    """
    if not ticker.strip():
        raise ValueError("ticker must not be empty")
    if not subreddits or any(not subreddit.strip() for subreddit in subreddits):
        raise ValueError("subreddits must contain non-empty names")
    if not 1 <= max_records <= _MAX_RECORDS:
        raise ValueError(f"max_records must be between 1 and {_MAX_RECORDS}")
    start_at = _boundary(start, is_end=False)
    end_at = _boundary(end, is_end=True)
    if start_at > end_at:
        raise ValueError("start must not be after end")

    client = session or requests.Session()
    evidence_ids: list[str] = []
    failures: list[str] = []
    artifact_hashes: list[str] = []
    requested = 0
    remaining = max_records
    for subreddit in subreddits:
        for kind, search_field in (("post", "query"), ("comment", "body")):
            if remaining == 0:
                break
            endpoint = f"{_API_BASE}/{kind}s/search"
            params = {
                "subreddit": subreddit,
                search_field: ticker,
                "after": start_at.date().isoformat(),
                "before": (end_at.date() + timedelta(days=1)).isoformat(),
                "sort": "asc",
                "limit": str(min(remaining, _MAX_PER_REQUEST)),
            }
            try:
                response = client.get(endpoint, params=params, timeout=30)
                response.raise_for_status()
                payload = response.json()
                rows = _rows(payload)
            except requests.RequestException as error:
                failures.append(f"{subreddit}:{kind}:{type(error).__name__}")
                continue
            except (ValueError, RedditArchiveResponseError) as error:
                failures.append(f"{subreddit}:{kind}:{type(error).__name__}")
                continue
            raw_hash = store.put_artifact(
                canonical_json(payload).encode("utf-8"), media_type="application/json"
            )
            artifact_hashes.append(raw_hash)
            requested += len(rows)
            for position, row in enumerate(rows, start=1):
                evidence_id = _record_row(
                    store,
                    row,
                    kind=kind,
                    ticker=ticker,
                    subreddit=subreddit,
                    raw_response_artifact_hash=raw_hash,
                )
                if evidence_id is None:
                    failures.append(f"{subreddit}:{kind}-{position}:invalid-record")
                else:
                    evidence_ids.append(evidence_id)
                    remaining -= 1
                    if remaining == 0:
                        break
    return RedditArchiveImportResult(
        requested=requested,
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
        response_artifact_hashes=tuple(artifact_hashes),
    )


def _rows(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise RedditArchiveResponseError("Reddit archive response was not an object")
    rows = payload.get("data")
    if rows is None and payload.get("error"):
        raise RedditArchiveResponseError("Reddit archive returned an error response")
    if rows is None:
        return []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RedditArchiveResponseError("Reddit archive response has invalid data rows")
    return rows


def _record_row(
    store: TemporalStore,
    row: dict[str, Any],
    *,
    kind: str,
    ticker: str,
    subreddit: str,
    raw_response_artifact_hash: str,
) -> str | None:
    identifier = row.get("id")
    created_utc = row.get("created_utc")
    row_subreddit = row.get("subreddit")
    if (
        not isinstance(identifier, str)
        or not identifier
        or isinstance(created_utc, bool)
        or not isinstance(created_utc, (int, float))
        or not isinstance(row_subreddit, str)
        or not row_subreddit
    ):
        return None
    title = row.get("title") if kind == "post" else ""
    body = row.get("selftext") if kind == "post" else row.get("body")
    if not isinstance(title, str):
        title = ""
    if not isinstance(body, str):
        body = ""
    published_at = datetime.fromtimestamp(created_utc, timezone.utc)
    permalink = row.get("permalink")
    source_url = (
        f"https://www.reddit.com{permalink}"
        if isinstance(permalink, str) and permalink.startswith("/")
        else f"https://www.reddit.com/r/{row_subreddit}/comments/{identifier}"
    )
    record = store.record(
        "corpus.document",
        {
            "source": "reddit-arctic-shift",
            "kind": kind,
            "external_id": identifier,
            "ticker": ticker.upper(),
            "subreddit": subreddit,
        },
        {
            "text": f"{title}\n\n{body}".strip(),
            "metadata": {
                "reddit": row,
                "kind": kind,
                "ticker_query": ticker.upper(),
                "raw_response_artifact_hash": raw_response_artifact_hash,
                "availability_basis": "reddit-created_utc",
                "original_content": "arctic-shift-archive",
            },
        },
        available_at=published_at,
        observed_at=published_at,
        event_at=published_at,
        source_published_at=published_at,
        fidelity="archive-reconstructed",
        source=source_url,
    )
    return record.evidence_id


def _boundary(value: str | datetime, *, is_end: bool) -> datetime:
    if isinstance(value, datetime):
        return parse_timestamp(value)
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        day = datetime.fromisoformat(value).date()
        return datetime.combine(day, time.max if is_end else time.min, tzinfo=timezone.utc)
    return parse_timestamp(value)
