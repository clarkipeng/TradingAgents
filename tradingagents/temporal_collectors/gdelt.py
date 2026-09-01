"""GDELT DOC API backfill for historical public-news discovery evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from time import sleep as _default_sleep
from typing import Any
from urllib.parse import urlencode

from tradingagents.dataflows.errors import ProviderResponseError, ProviderTransientError
from tradingagents.dataflows.gdelt_common import (
    GDELT_DOC_API_URL,
    article_list_params,
    normalize_gdelt_query,
    pace_gdelt_request,
)
from tradingagents.dataflows.provider_http import get_json
from tradingagents.temporal import TemporalStore, canonical_json, parse_timestamp

_DOC_API_URL = GDELT_DOC_API_URL
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


# GDELT 429s arrive in multi-request bursts even at a compliant cadence
# (measured live 2026-08-23: alternating 200/429 runs at one request per 6s),
# so retries escalate their wait instead of re-asking inside the same burst.
_GDELT_RETRY_EXTRA_WAITS_SECONDS = (0.0, 30.0, 120.0)


def _paced_doc_request(url: str, sleep: Any = _default_sleep) -> Any:
    """Fetch one DOC API response with every attempt paced.

    GDELT allows one request every 5 seconds and its 429s carry no
    Retry-After, so an unpaced in-transport retry would itself violate the
    quota and poison the window for the next query. Each attempt goes back
    through the shared pace gate, with escalating extra waits to outlast
    server-side 429 bursts.
    """
    last_error: ProviderTransientError | None = None
    for extra_wait in _GDELT_RETRY_EXTRA_WAITS_SECONDS:
        if extra_wait:
            sleep(extra_wait)
        pace_gdelt_request()
        try:
            return get_json(url, timeout=30, attempts=1, max_bytes=1_000_000)
        except ProviderTransientError as error:
            last_error = error
    raise last_error


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
    query = normalize_gdelt_query(query)
    if not 1 <= max_records <= _MAX_RECORDS:
        raise ValueError(f"max_records must be between 1 and {_MAX_RECORDS}")
    start_at = _boundary(start, is_end=False)
    end_at = _boundary(end, is_end=True)
    if start_at > end_at:
        raise ValueError("start must not be after end")
    params = article_list_params(
        query,
        start=_gdelt_timestamp(start_at),
        end=_gdelt_timestamp(end_at),
        max_records=max_records,
    )
    if session is None:
        try:
            payload = _paced_doc_request(
                f"{_DOC_API_URL}?{urlencode(sorted(params.items()))}"
            )
        except (ProviderResponseError, ProviderTransientError) as error:
            raise GdeltResponseError(str(error)) from error
    else:
        pace_gdelt_request()
        response = session.get(_DOC_API_URL, params=params, timeout=30)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise GdeltResponseError("GDELT returned a non-JSON response (often a rate-limit message)") from error
    fetch_receipt = datetime.now(timezone.utc)
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise GdeltResponseError("GDELT response has no article list")
    raw_response_artifact_hash = store.put_artifact(
        canonical_json(payload).encode("utf-8"), media_type="application/json"
    )

    evidence_ids: list[str] = []
    failures: list[str] = []
    # Clustering waits for the nightly rebuild; per-insert refreshes scan the
    # whole corpus and cannot be paid inside a paced nightly sweep.
    with store.deferred_clustering(flush=False):
        _import_gdelt_article_rows(
            store, payload, query, raw_response_artifact_hash, fetch_receipt, evidence_ids, failures
        )
    return GdeltImportResult(
        requested=len(payload["articles"]),
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
        response_artifact_hash=raw_response_artifact_hash,
    )


def _import_gdelt_article_rows(
    store, payload, query, raw_response_artifact_hash, fetch_receipt, evidence_ids, failures
):
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
            },
            {
                "text": title,
                "metadata": {
                    "article": {key: value for key, value in article.items() if key != "seendate"},
                    "query": query,
                    "raw_response_artifact_hash": raw_response_artifact_hash,
                    "available_at_policy": "fetch-receipt",
                    "availability_basis": "gdelt-fetch-receipt",
                    "provider_available_at_estimate": seen_at.isoformat(),
                    "original_content": "not-fetched",
                },
            },
            available_at=fetch_receipt,
            observed_at=fetch_receipt,
            fidelity="archive-reconstructed",
            source=url,
        )
        evidence_ids.append(record.evidence_id)


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
