"""Bridge existing poller media into the canonical temporal corpus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from tradingagents.dataflows.media_store import open_store
from tradingagents.temporal import TemporalStore

_MAX_RECORDS = 10_000


@dataclass(frozen=True)
class MediaStoreImportResult:
    requested: int
    imported: int
    evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]


def import_media_store_posts(
    temporal_store: TemporalStore,
    *,
    start: str,
    end: str,
    media_db_url: str | None = None,
    sources: tuple[str, ...] = (),
    tickers: tuple[str, ...] = (),
    limit: int = 1_000,
) -> MediaStoreImportResult:
    """Copy look-ahead-safe poller rows into per-post temporal documents.

    This is a one-way migration bridge: the media store stays a poller staging
    database, while all new replay/search consumers use ``temporal_store``.
    A post becomes searchable at its poller fetch receipt, never merely at its
    source timestamp; the latter is retained as event and publication metadata.
    """
    if not 1 <= limit <= _MAX_RECORDS:
        raise ValueError(f"limit must be between 1 and {_MAX_RECORDS}")
    source_store = open_store(media_db_url)
    try:
        rows = source_store.history_asof(
            start,
            end,
            tickers=list(tickers) or None,
            sources=list(sources) or None,
            limit=limit,
        )
    finally:
        source_store.close()

    evidence_ids: list[str] = []
    failures: list[str] = []
    # The temporal store has one mutator at a time by invariant; without the
    # lock this import deadlocks against the live poller mirror. Clustering
    # defers to one batch refresh per chunk - per-row refreshes made bulk
    # imports pay a full-corpus scan per record. The lock is taken per chunk,
    # not per import, so a long backfill never starves live writers (observed
    # live: a graph run lost a ticker to OperationalError while a backfill
    # held the lock for minutes).
    chunk_size = 500
    for start_index in range(0, len(rows), chunk_size):
        chunk = rows[start_index:start_index + chunk_size]
        with temporal_store.write_lock(), temporal_store.deferred_clustering():
            for offset, row in enumerate(chunk, start=start_index + 1):
                evidence_id = _import_row(temporal_store, row)
                if evidence_id is None:
                    failures.append(f"post-{offset}:invalid-record")
                else:
                    evidence_ids.append(evidence_id)
    return MediaStoreImportResult(
        requested=len(rows),
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
    )


def _import_row(store: TemporalStore, row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    source = row.get("source")
    external_id = row.get("external_id")
    title = row.get("title") or ""
    body = row.get("body") or ""
    created_utc = row.get("created_utc")
    fetched_utc = row.get("fetched_utc")
    if (
        not isinstance(source, str)
        or not source
        or not isinstance(external_id, str)
        or not external_id
        or isinstance(created_utc, bool)
        or not isinstance(created_utc, (int, float))
        or isinstance(fetched_utc, bool)
        or not isinstance(fetched_utc, (int, float))
        or not isinstance(title, str)
        or not isinstance(body, str)
    ):
        return None
    published_at = datetime.fromtimestamp(created_utc, timezone.utc)
    observed_at = datetime.fromtimestamp(fetched_utc, timezone.utc)
    record = store.record(
        "corpus.document",
        {
            "source": "poller-media-store",
            "media_source": source,
            "external_id": external_id,
            "fetched_utc": fetched_utc,
        },
        {
            "text": f"{title}\n\n{body}".strip(),
            "metadata": {
                "media_post": row,
                "availability_basis": "poller-fetch-receipt",
            },
        },
        available_at=observed_at,
        observed_at=observed_at,
        event_at=published_at,
        source_published_at=published_at,
        fidelity="forward-captured",
        source=f"{source}:{external_id}",
    )
    return record.evidence_id
