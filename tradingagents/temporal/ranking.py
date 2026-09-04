"""Deterministic eligible-corpus lexical ranking for temporal retrieval.

The eligible index stores compact per-chunk statistics (term postings with
title/body counts, lengths, precomputed recency, alias flags) instead of the
chunk text and token tuples. Scores and the index digest are exactly what the
materialized-chunk implementation produced: the digest streams the same
canonical JSON bytes, and rank() evaluates the same arithmetic in the same
order over the same candidate set. Memory is O(postings), not O(corpus text),
and a query touches only chunks that share a term with it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from array import array
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .models import canonical_json

RANKER_VERSION = "temporal-hybrid-v3"
# v4: the digest covers exactly what determines ranking - the eligible
# document chunks at the cutoff. v3 embedded the whole-evidence corpus_hash,
# which a live run's own tool tapes advance on every call, so one day run
# produced dozens of "distinct" indexes of identical documents (a 255s
# rebuild each on the trader machine).
INDEX_VERSION = "eligible-chunk-index-v4"
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
class EligibleChunkIndex:
    as_of: str
    index_state_hash: str
    n_chunks: int
    doc_keys: tuple[str, ...]
    cluster_by_doc_key: dict[str, str]
    # term -> flat (chunk_index, title_count, body_count) triples. A term has
    # an entry for a chunk exactly when it appears in the chunk's title or
    # body, which is the old set-membership df definition.
    postings: dict[str, array]
    title_lens: array
    body_lens: array
    recency_weights: array
    has_alias: bytes
    sum_title_lens: int
    sum_body_lens: int


def build_eligible_index(connection: sqlite3.Connection, *, as_of: str) -> EligibleChunkIndex:
    document_columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    chunk_columns = {row[1] for row in connection.execute("PRAGMA table_info(document_chunks)")}
    cluster = "d.cluster_key" if "cluster_key" in document_columns else "d.doc_key"
    title = "d.title" if "title" in document_columns else "''"
    published = "d.published_at" if "published_at" in document_columns else "NULL"
    chunk_order = "c.chunk_index" if "chunk_index" in chunk_columns else "c.rowid"
    cursor = connection.execute(
        f"""SELECT c.doc_key, d.parent_evidence_id, {cluster} AS cluster_key, {title} AS title, c.body,
                  d.available_at, {published} AS published_at
           FROM document_chunks AS c JOIN documents AS d ON d.doc_key = c.doc_key
           WHERE d.available_at <= ? ORDER BY c.doc_key, {chunk_order}""",
        (as_of,),
    )

    # The digest streams the exact bytes canonical_json produced for the old
    # state dict {"as_of", "chunks", "corpus_hash", "version"} (sorted keys),
    # so sealed manifests keep verifying without holding the corpus in memory.
    digest = hashlib.sha256()
    digest.update(('{"as_of":' + json.dumps(as_of, ensure_ascii=False) + ',"chunks":[').encode("utf-8"))

    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    doc_keys: list[str] = []
    cluster_by_doc_key: dict[str, str] = {}
    postings: dict[str, array] = {}
    title_lens = array("l")
    body_lens = array("l")
    recency_weights = array("d")
    has_alias = bytearray()
    sum_title = 0
    sum_body = 0
    first = True
    for idx, row in enumerate(cursor):
        if not first:
            digest.update(b",")
        first = False
        digest.update(canonical_json([
            row["doc_key"], row["parent_evidence_id"], row["title"], row["body"],
            row["available_at"], row["published_at"],
        ]).encode("utf-8"))

        title_terms = _terms(row["title"])
        body_terms = _terms(row["body"])
        doc_keys.append(row["doc_key"])
        cluster_by_doc_key.setdefault(row["doc_key"], row["cluster_key"])
        title_lens.append(len(title_terms))
        body_lens.append(len(body_terms))
        sum_title += len(title_terms)
        sum_body += len(body_terms)
        title_counts = Counter(title_terms)
        body_counts = Counter(body_terms)
        for term in title_counts.keys() | body_counts.keys():
            entry = postings.get(term)
            if entry is None:
                entry = postings[term] = array("l")
            entry.append(idx)
            entry.append(title_counts.get(term, 0))
            entry.append(body_counts.get(term, 0))
        has_alias.append(1 if any(term in STATIC_STORE_ALIASES for term in title_counts.keys() | body_counts.keys()) else 0)
        observed = datetime.fromisoformat((row["published_at"] or row["available_at"]).replace("Z", "+00:00"))
        age_days = max(0.0, (cutoff - observed).total_seconds() / 86400)
        recency_weights.append(math.exp(-math.log(2) * age_days / RECENCY_HALF_LIFE_DAYS))

    digest.update((
        '],"version":' + json.dumps(INDEX_VERSION, ensure_ascii=False) + "}"
    ).encode("utf-8"))
    return EligibleChunkIndex(
        as_of=as_of, index_state_hash=digest.hexdigest(),
        n_chunks=len(doc_keys), doc_keys=tuple(doc_keys), cluster_by_doc_key=cluster_by_doc_key,
        postings=postings, title_lens=title_lens, body_lens=body_lens,
        recency_weights=recency_weights, has_alias=bytes(has_alias),
        sum_title_lens=sum_title, sum_body_lens=sum_body,
    )


def rank(index: EligibleChunkIndex, query: str, limit: int) -> list[tuple[str, float]]:
    query_terms = tuple(dict.fromkeys(_terms(query)))
    if not query_terms:
        return []
    n = index.n_chunks
    if not n:
        return []
    df: dict[str, int] = {}
    candidates: dict[int, dict[str, tuple[int, int]]] = {}
    for term in query_terms:
        entries = index.postings.get(term)
        if entries is None:
            df[term] = 0
            continue
        df[term] = len(entries) // 3
        for offset in range(0, len(entries), 3):
            chunk = entries[offset]
            candidates.setdefault(chunk, {})[term] = (entries[offset + 1], entries[offset + 2])
    avg_title = index.sum_title_lens / n
    avg_body = index.sum_body_lens / n
    k1, b = 1.2, 0.75
    query_has_alias = any(term in STATIC_STORE_ALIASES for term in query_terms)
    scored: dict[str, tuple[float, str]] = {}
    for chunk, counts in candidates.items():
        title_len = index.title_lens[chunk]
        body_len = index.body_lens[chunk]
        score = 0.0
        for term in query_terms:
            title_count, body_count = counts.get(term, (0, 0))
            if not title_count and not body_count:
                continue
            idf = math.log1p((n - df[term] + 0.5) / (df[term] + 0.5))
            title_norm = title_count * (k1 + 1) / (title_count + k1 * (1 - b + b * title_len / max(avg_title, 1)))
            body_norm = body_count * (k1 + 1) / (body_count + k1 * (1 - b + b * body_len / max(avg_body, 1)))
            score += idf * (TITLE_WEIGHT * title_norm + BODY_WEIGHT * body_norm)
        if score <= 0:
            continue
        if query_has_alias and index.has_alias[chunk]:
            score *= 1.25
        score *= index.recency_weights[chunk]
        doc_key = index.doc_keys[chunk]
        current = scored.get(doc_key)
        if current is None or (score, doc_key) > (current[0], current[1]):
            scored[doc_key] = (score, doc_key)
    return [(key, value[0]) for key, value in sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))[:limit]]
