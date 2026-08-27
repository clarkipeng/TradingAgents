"""One-way projection from the production media poller into temporal evidence."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from tradingagents.temporal import TemporalStore

_TEMPORAL_STORE_ENV = "TRADINGAGENTS_POLLER_TEMPORAL_STORE"


def mirror_poller_media_fetch(
    rows: Sequence[Mapping[str, Any]],
    *,
    provider: str,
    query_key: str,
    fetch_run_id: str,
    received_utc: float,
) -> int:
    """Project one terminal poller receipt into searchable temporal documents.

    The mirror is deliberately opt-in through ``TRADINGAGENTS_POLLER_TEMPORAL_STORE``.
    It runs only after the poller's own terminal receipt commits, never affects
    provider budgets, and makes each item available at that receipt's observed
    time. Repeating the same completed fetch is idempotent by request/content
    identity.
    """
    root = os.getenv(_TEMPORAL_STORE_ENV)
    if not root:
        return 0
    store = TemporalStore(root)
    observed_at = datetime.fromtimestamp(received_utc, timezone.utc)
    with store.write_lock(), store.deferred_clustering():
        imported = 0
        for row in rows:
            evidence_id = _record_row(
                store,
                row,
                provider=provider,
                query_key=query_key,
                fetch_run_id=fetch_run_id,
                observed_at=observed_at,
            )
            if evidence_id is not None:
                imported += 1
    return imported


def _record_row(
    store: TemporalStore,
    row: Mapping[str, Any],
    *,
    provider: str,
    query_key: str,
    fetch_run_id: str,
    observed_at: datetime,
) -> str | None:
    source = row.get("source")
    external_id = row.get("external_id")
    created_utc = row.get("created_utc")
    title = row.get("title") or ""
    body = row.get("body") or ""
    if (
        not isinstance(source, str)
        or not source
        or not isinstance(external_id, str)
        or not external_id
        or isinstance(created_utc, bool)
        or not isinstance(created_utc, (int, float))
        or not isinstance(title, str)
        or not isinstance(body, str)
    ):
        return None
    published_at = datetime.fromtimestamp(created_utc, timezone.utc)
    record = store.record(
        "corpus.document",
        {
            "source": "poller-temporal-mirror",
            "provider": provider,
            "query_key": query_key,
            "fetch_run_id": fetch_run_id,
            "media_source": source,
            "external_id": external_id,
        },
        {
            "text": f"{title}\n\n{body}".strip(),
            "metadata": {
                "media_post": dict(row),
                "poller_provider": provider,
                "poller_query_key": query_key,
                "poller_fetch_run_id": fetch_run_id,
                "availability_basis": "poller-terminal-receipt",
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
