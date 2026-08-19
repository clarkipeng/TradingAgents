"""Rebuildable normalized documents derived from immutable evidence."""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

EXTRACTOR_VERSION = "document-extractors-v1"
CHUNKER_VERSION = "token-window-1500-overlap-200-v1"
_WORD_RE = re.compile(r"\S+")
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}


def stable_doc_key(parent_evidence_id: str, logical_position: int = 0) -> str:
    return hashlib.sha256(f"{parent_evidence_id}:{logical_position}".encode()).hexdigest()


def stable_chunk_id(doc_key: str, chunk_index: int) -> str:
    return hashlib.sha256(f"{CHUNKER_VERSION}:{doc_key}:{chunk_index}".encode()).hexdigest()


def canonical_url(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return url.strip()
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _TRACKING))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), parts.path or "/", query, ""))


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sec_body(value: str) -> str:
    value = re.sub(r"<SEC-HEADER>.*?</SEC-HEADER>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<DOCUMENT>.*?<TEXT>(.*?)</TEXT>.*?</DOCUMENT>", r"\1", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def extract_document(evidence: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize the four supported source shapes without mutating evidence."""
    tool = evidence["tool"]
    response = evidence["response"] if isinstance(evidence["response"], dict) else {}
    request = evidence["request"] if isinstance(evidence["request"], dict) else {}
    metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
    if tool != "corpus.document" or evidence.get("is_error"):
        return None
    title = _text(response.get("title"))
    body = _text(response.get("body"))
    text = _text(response.get("text"))
    source = _text(evidence.get("source")) or _text(request.get("source"))
    if metadata.get("article") and isinstance(metadata["article"], dict):
        article = metadata["article"]
        title = title or _text(article.get("title"))
        source = source or _text(article.get("url"))
        body = body or title  # GDELT discovery is intentionally title-only.
        kind = "gdelt"
    elif metadata.get("story") and isinstance(metadata["story"], dict):
        story = metadata["story"]
        title = title or _text(story.get("title") or story.get("story_title"))
        body = body or text
        kind = "hacker-news"
    elif metadata.get("form") or metadata.get("accession_number"):
        kind = "sec"
        body = body or _sec_body(text)
    elif metadata.get("wayback_url") or metadata.get("original_url"):
        kind = "wayback"
        body = body or text
    else:
        kind = "document"
        body = body or text
    if not body and not title:
        return None
    if not title:
        title = body.splitlines()[0][:500] if body else "Untitled document"
    return {
        "title": title,
        "body": body,
        "source_domain": (urlsplit(canonical_url(source) or "").hostname or "") if source else "",
        "canonical_url": canonical_url(source) or canonical_url(request.get("url")) or canonical_url(metadata.get("original_url")),
        "published_at": evidence.get("source_published_at") or evidence.get("event_at"),
        "available_at": evidence["available_at"],
        "doc_kind": kind,
        "extractor_version": EXTRACTOR_VERSION,
    }


def title_similarity(left: str, right: str) -> float:
    def normalize(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.lower()))
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def chunks(body: str) -> list[tuple[int, int, str]]:
    words = _WORD_RE.findall(body)
    if not words:
        return []
    size, overlap = 1500, 200
    result = []
    start = 0
    while start < len(words):
        end = min(len(words), start + size)
        result.append((start, end, " ".join(words[start:end])))
        if end == len(words):
            break
        start = end - overlap
    return result
