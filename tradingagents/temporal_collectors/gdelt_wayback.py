"""Bounded body recovery for GDELT historical-news discovery records."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import requests

from tradingagents.temporal import TemporalStore

from .gdelt import GdeltImportResult, import_gdelt_articles
from .wayback import import_wayback_captures


@dataclass(frozen=True)
class GdeltWaybackImportResult:
    """A GDELT discovery import plus the Wayback bodies recovered from it."""

    discovery: GdeltImportResult
    attempted: int
    imported: int
    evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]


def import_gdelt_wayback_bodies(
    store: TemporalStore,
    *,
    query: str,
    start: str,
    end: str,
    max_records: int = 25,
    max_capture_lag_days: int = 7,
    request_delay_seconds: float = 1.0,
    gdelt_session: Any | None = None,
    wayback_session: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> GdeltWaybackImportResult:
    """Recover one archived body per discovered article in a causal window.

    GDELT retains the discovery result first. For every valid discovered URL,
    this collector asks Wayback only for captures from the GDELT ``seendate``
    through a small later window. The body record therefore remains available
    at its actual Wayback capture clock rather than being backdated to GDELT.
    A lineage object joins the body to the exact discovery record and query.
    """
    if not 1 <= max_records <= 250:
        raise ValueError("max_records must be between 1 and 250")
    if max_capture_lag_days < 0:
        raise ValueError("max_capture_lag_days must not be negative")
    if request_delay_seconds < 0.1:
        raise ValueError("request_delay_seconds must be at least 0.1")

    discovery = import_gdelt_articles(
        store,
        query=query,
        start=start,
        end=end,
        max_records=max_records,
        session=gdelt_session,
    )
    evidence_ids: list[str] = []
    failures: list[str] = []
    for position, discovery_id in enumerate(discovery.evidence_ids):
        if position:
            sleep(request_delay_seconds)
        record = store.get_evidence(discovery_id)
        url = record.request.get("url")
        if not isinstance(url, str) or not url:
            failures.append(f"{discovery_id}:missing-url")
            continue
        capture_start = record.available_at
        capture_end = capture_start + timedelta(days=max_capture_lag_days)
        try:
            recovered = import_wayback_captures(
                store,
                url=url,
                start=capture_start.strftime("%Y%m%d%H%M%S"),
                end=capture_end.strftime("%Y%m%d%H%M%S"),
                max_captures=1,
                request_delay_seconds=request_delay_seconds,
                session=wayback_session,
                sleep=sleep,
                lineage={
                    "discovery_evidence_id": discovery_id,
                    "discovery_source": "gdelt-doc-2",
                    "discovery_query": query,
                    "discovery_available_at": record.available_at.isoformat(),
                },
            )
        except (requests.RequestException, ValueError) as error:
            failures.append(f"{discovery_id}:{type(error).__name__}")
            continue
        evidence_ids.extend(recovered.evidence_ids)
        failures.extend(f"{discovery_id}:{failure}" for failure in recovered.failures)
    return GdeltWaybackImportResult(
        discovery=discovery,
        attempted=len(discovery.evidence_ids),
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
    )
