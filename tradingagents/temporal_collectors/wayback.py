"""Wayback CDX backfill for public web evidence.

The archive capture time is a conservative proxy for when a crawler could have
seen a public page. It is not a claim about an original search engine's index,
so every result remains explicitly ``archive-reconstructed``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import requests

from tradingagents.temporal import TemporalStore

_CDX_URL = "https://web.archive.org/cdx/search/cdx"
_TIMESTAMP = re.compile(r"^\d{14}$")
_CDX_COLUMNS = ("timestamp", "original", "statuscode", "mimetype", "digest")


@dataclass(frozen=True)
class WaybackImportResult:
    requested: int
    imported: int
    evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]


class _TextExtractor(HTMLParser):
    """Small dependency-free HTML text derivative; raw bytes are preserved separately."""

    _IGNORED = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._IGNORED:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def import_wayback_captures(
    store: TemporalStore,
    *,
    url: str,
    start: str | None = None,
    end: str | None = None,
    max_captures: int = 25,
    request_delay_seconds: float = 1.0,
    session: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> WaybackImportResult:
    """Fetch deduplicated archived HTML captures into the temporal corpus.

    ``url`` is passed to the CDX index and may be an exact URL or its supported
    wildcard form. Each page's original HTML bytes are stored as a separate
    content-addressed artifact; the searchable evidence holds a lightweight
    text derivative plus the raw artifact address.
    """
    if not url.strip():
        raise ValueError("url must not be empty")
    if max_captures < 1:
        raise ValueError("max_captures must be positive")
    if request_delay_seconds < 0.1:
        raise ValueError("request_delay_seconds must be at least 0.1")
    start_timestamp = _normalize_cdx_boundary(start, "start")
    end_timestamp = _normalize_cdx_boundary(end, "end")
    if start_timestamp and end_timestamp and start_timestamp > end_timestamp:
        raise ValueError("start must not be after end")

    client = session or requests.Session()
    params: dict[str, Any] = {
        "url": url,
        "output": "json",
        "fl": ",".join(_CDX_COLUMNS),
        "filter": ["statuscode:200", "mimetype:text/html"],
        "collapse": "digest",
        "limit": str(max_captures),
    }
    if start_timestamp:
        params["from"] = start_timestamp
    if end_timestamp:
        params["to"] = end_timestamp
    index_response = client.get(_CDX_URL, params=params, timeout=30)
    index_response.raise_for_status()
    captures = _parse_cdx_rows(index_response.json())[:max_captures]

    evidence_ids: list[str] = []
    failures: list[str] = []
    for position, capture in enumerate(captures):
        if position:
            sleep(request_delay_seconds)
        timestamp = capture["timestamp"]
        original_url = capture["original"]
        replay_url = f"https://web.archive.org/web/{timestamp}id_/{original_url}"
        try:
            page_response = client.get(replay_url, timeout=30)
            page_response.raise_for_status()
            raw_html = page_response.content
            media_type = page_response.headers.get("Content-Type", "text/html").split(";", 1)[0]
            raw_artifact_hash = store.put_artifact(raw_html, media_type=media_type)
            text = _extract_text(page_response.text)
            captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            record = store.record(
                "corpus.document",
                {
                    "source": "wayback",
                    "original_url": original_url,
                    "capture_timestamp": timestamp,
                    "digest": capture.get("digest"),
                },
                {
                    "text": text,
                    "metadata": {
                        "original_url": original_url,
                        "wayback_url": replay_url,
                        "capture_timestamp": timestamp,
                        "digest": capture.get("digest"),
                        "raw_artifact_hash": raw_artifact_hash,
                        "raw_media_type": media_type,
                        "availability_basis": "wayback-capture",
                    },
                },
                available_at=captured_at,
                source_published_at=None,
                fidelity="archive-reconstructed",
                source=replay_url,
            )
        except requests.RequestException as error:
            failures.append(f"{timestamp}:{type(error).__name__}")
        else:
            evidence_ids.append(record.evidence_id)
    return WaybackImportResult(
        requested=len(captures),
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
    )


def _normalize_cdx_boundary(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = value.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
    if not normalized.isdigit() or not 4 <= len(normalized) <= 14:
        raise ValueError(f"{name} must be a CDX timestamp or ISO date/time")
    return normalized


def _parse_cdx_rows(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, list) or not payload:
        return []
    header = payload[0]
    if not isinstance(header, list) or not {"timestamp", "original"} <= set(header):
        raise ValueError("Wayback CDX response has no timestamp/original header")
    captures: list[dict[str, str]] = []
    for row in payload[1:]:
        if not isinstance(row, list):
            continue
        item = {str(column): str(row[index]) for index, column in enumerate(header) if index < len(row)}
        if _TIMESTAMP.fullmatch(item.get("timestamp", "")) and item.get("original"):
            captures.append(item)
    return captures


def _extract_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join(" ".join(parser.parts).split())
