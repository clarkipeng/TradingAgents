"""Small, explicit importers for archive-derived temporal corpus evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .clock import parse_timestamp
from .store import TemporalStore


@dataclass(frozen=True)
class ImportSummary:
    imported: int
    evidence_ids: tuple[str, ...]


def import_archive_jsonl(path: str | Path, store: TemporalStore) -> ImportSummary:
    """Import a transparent archive corpus into searchable evidence.

    Each line must contain ``source_url``, ``available_at``, and ``document``.
    Optional ``external_id`` keeps multiple historical versions of one URL
    distinct. Imported evidence is always labelled ``archive-reconstructed``.
    """
    evidence_ids: list[str] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            try:
                source_url = item["source_url"]
                available_at = parse_timestamp(item["available_at"])
                document = item["document"]
            except KeyError as error:
                raise ValueError(f"archive line {line_number} is missing {error.args[0]!r}") from error
            record = store.record(
                "corpus.document",
                {"source_url": source_url, "external_id": item.get("external_id")},
                document,
                available_at=available_at,
                observed_at=available_at,
                event_at=(parse_timestamp(item["event_at"]) if item.get("event_at") else None),
                source_published_at=(
                    parse_timestamp(item["source_published_at"])
                    if item.get("source_published_at")
                    else None
                ),
                fidelity="archive-reconstructed",
                source=source_url,
            )
            evidence_ids.append(record.evidence_id)
    return ImportSummary(imported=len(evidence_ids), evidence_ids=tuple(evidence_ids))
