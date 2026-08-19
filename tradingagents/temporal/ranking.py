"""Deterministic eligible-corpus lexical ranking for temporal retrieval."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .models import canonical_json

RANKER_VERSION = "temporal-hybrid-v3"
INDEX_VERSION = "eligible-chunk-index-v3"
TITLE_WEIGHT = 2.0
BODY_WEIGHT = 1.0
RECENCY_HALF_LIFE_DAYS = 30.0
_TOKEN = re.compile(r"[\w]+", re.UNICODE)

# Deliberately static: replay and ranking must never consult a live symbol service.
STATIC_STORE_ALIASES: dict[str, tuple[str, ...]] = {
    "nvda": ("nvda", "nvidia"),
    "tsla": ("tsla", "tesla"),
    "msft": ("msft", "microsoft"),
    "aapl": ("aapl", "apple"),
    "amzn": ("amzn", "amazon"),
    "googl": ("googl", "google", "alphabet"),
    "meta": ("meta", "facebook"),
}
_ALIAS_TO_CANONICAL = {alias: canonical for canonical, aliases in STATIC_STORE_ALIASES.items() for alias in aliases}


def _terms(value: str) -> list[str]:
    return [_ALIAS_TO_CANONICAL.get(token.lower(), token.lower()) for token in _TOKEN.findall(value)]


@dataclass(frozen=True)
class IndexedChunk:
    doc_key: str
    parent_evidence_id: str
    cluster_key: str
    title: str
    body: str
    available_at: str
    published_at: str | None
    title_terms: tuple[str, ...]
    body_terms: tuple[str, ...]


@dataclass(frozen=True)
class EligibleChunkIndex:
    corpus_hash: str
    as_of: str
    chunks: tuple[IndexedChunk, ...]
    index_state_hash: str


def build_eligible_index(connection: sqlite3.Connection, *, corpus_hash: str, as_of: str) -> EligibleChunkIndex:
    document_columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    chunk_columns = {row[1] for row in connection.execute("PRAGMA table_info(document_chunks)")}
    cluster = "d.cluster_key" if "cluster_key" in document_columns else "d.doc_key"
    title = "d.title" if "title" in document_columns else "''"
    published = "d.published_at" if "published_at" in document_columns else "NULL"
    chunk_order = "c.chunk_index" if "chunk_index" in chunk_columns else "c.rowid"
    rows = connection.execute(
        f"""SELECT c.doc_key, d.parent_evidence_id, {cluster} AS cluster_key, {title} AS title, c.body,
                  d.available_at, {published} AS published_at
           FROM document_chunks AS c JOIN documents AS d ON d.doc_key = c.doc_key
           WHERE d.available_at <= ? ORDER BY c.doc_key, {chunk_order}""",
        (as_of,),
    ).fetchall()
    chunks = tuple(
        IndexedChunk(
            doc_key=row["doc_key"], parent_evidence_id=row["parent_evidence_id"],
            cluster_key=row["cluster_key"], title=row["title"], body=row["body"],
            available_at=row["available_at"], published_at=row["published_at"],
            title_terms=tuple(_terms(row["title"])), body_terms=tuple(_terms(row["body"])),
        ) for row in rows
    )
    state = {
        "version": INDEX_VERSION, "corpus_hash": corpus_hash, "as_of": as_of,
        "chunks": [(c.doc_key, c.parent_evidence_id, c.title, c.body, c.available_at, c.published_at) for c in chunks],
    }
    digest = hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()
    return EligibleChunkIndex(corpus_hash, as_of, chunks, digest)


def rank(index: EligibleChunkIndex, query: str, limit: int) -> list[tuple[str, float]]:
    query_terms = tuple(dict.fromkeys(_terms(query)))
    if not query_terms:
        return []
    chunks = index.chunks
    if not chunks:
        return []
    term_sets = [set(c.title_terms + c.body_terms) for c in chunks]
    df = {term: sum(term in terms for terms in term_sets) for term in query_terms}
    avg_title = sum(len(c.title_terms) for c in chunks) / len(chunks)
    avg_body = sum(len(c.body_terms) for c in chunks) / len(chunks)
    k1, b = 1.2, 0.75
    scored: dict[str, tuple[float, str]] = {}
    cutoff = datetime.fromisoformat(index.as_of.replace("Z", "+00:00"))
    for chunk in chunks:
        title_counts = {term: chunk.title_terms.count(term) for term in query_terms}
        body_counts = {term: chunk.body_terms.count(term) for term in query_terms}
        score = 0.0
        for term in query_terms:
            if not title_counts[term] and not body_counts[term]:
                continue
            idf = math.log1p((len(chunks) - df[term] + 0.5) / (df[term] + 0.5))
            title_norm = title_counts[term] * (k1 + 1) / (title_counts[term] + k1 * (1 - b + b * len(chunk.title_terms) / max(avg_title, 1)))
            body_norm = body_counts[term] * (k1 + 1) / (body_counts[term] + k1 * (1 - b + b * len(chunk.body_terms) / max(avg_body, 1)))
            score += idf * (TITLE_WEIGHT * title_norm + BODY_WEIGHT * body_norm)
        if score <= 0:
            continue
        if any(term in STATIC_STORE_ALIASES for term in query_terms) and any(term in STATIC_STORE_ALIASES for term in chunk.title_terms + chunk.body_terms):
            score *= 1.25
        observed = datetime.fromisoformat((chunk.published_at or chunk.available_at).replace("Z", "+00:00"))
        age_days = max(0.0, (cutoff - observed).total_seconds() / 86400)
        score *= math.exp(-math.log(2) * age_days / RECENCY_HALF_LIFE_DAYS)
        current = scored.get(chunk.doc_key)
        if current is None or (score, chunk.doc_key) > (current[0], current[1]):
            scored[chunk.doc_key] = (score, chunk.doc_key)
    return [(key, value[0]) for key, value in sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))[:limit]]
