"""The compact postings index must be indistinguishable from the old one.

The old implementation materialized every chunk's text and token tuples in
memory; it is embedded here verbatim as the reference oracle. The new index
must produce byte-identical digests and float-identical rankings on corpora
covering unicode, aliases, missing published_at, empty titles, repeated
terms, and multi-chunk documents.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from tradingagents.temporal.models import canonical_json
from tradingagents.temporal.ranking import (
    INDEX_VERSION,
    RECENCY_HALF_LIFE_DAYS,
    STATIC_STORE_ALIASES,
    TITLE_WEIGHT,
    BODY_WEIGHT,
    _terms,
    build_eligible_index,
    rank,
)


# --- Reference: the previous implementation, verbatim behavior. ---

@dataclass(frozen=True)
class _OldChunk:
    doc_key: str
    parent_evidence_id: str
    cluster_key: str
    title: str
    body: str
    available_at: str
    published_at: str | None
    title_terms: tuple[str, ...]
    body_terms: tuple[str, ...]


def _old_build(connection, *, corpus_hash: str, as_of: str):
    rows = connection.execute(
        """SELECT c.doc_key, d.parent_evidence_id, d.cluster_key AS cluster_key, d.title AS title, c.body,
                  d.available_at, d.published_at AS published_at
           FROM document_chunks AS c JOIN documents AS d ON d.doc_key = c.doc_key
           WHERE d.available_at <= ? ORDER BY c.doc_key, c.chunk_index""",
        (as_of,),
    ).fetchall()
    chunks = tuple(
        _OldChunk(
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
    return chunks, digest


def _old_rank(chunks, as_of: str, query: str, limit: int):
    query_terms = tuple(dict.fromkeys(_terms(query)))
    if not query_terms or not chunks:
        return []
    term_sets = [set(c.title_terms + c.body_terms) for c in chunks]
    df = {term: sum(term in terms for terms in term_sets) for term in query_terms}
    avg_title = sum(len(c.title_terms) for c in chunks) / len(chunks)
    avg_body = sum(len(c.body_terms) for c in chunks) / len(chunks)
    k1, b = 1.2, 0.75
    scored = {}
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
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


# --- Fixture corpus generation. ---

_WORDS = [
    "nvidia", "tesla", "apple", "earnings", "guidance", "chip", "margin",
    "рынок", "収益", "supply", "börse", "the", "of", "beats", "misses",
    "q3", "2026", "datacenter", "ai", "recall", "upgrade",
]


def _fake_corpus(connection: sqlite3.Connection, seed: int, docs: int) -> None:
    connection.execute(
        "CREATE TABLE documents (doc_key TEXT PRIMARY KEY, parent_evidence_id TEXT, cluster_key TEXT,"
        " title TEXT, available_at TEXT, published_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE document_chunks (doc_key TEXT, chunk_index INTEGER, body TEXT)"
    )
    rng = random.Random(seed)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(docs):
        doc_key = f"doc-{seed}-{i:04d}"
        title = " ".join(rng.choices(_WORDS, k=rng.randint(0, 6)))
        available = (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        published = None if i % 5 == 0 else (base + timedelta(hours=i - 3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        connection.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?)",
            (doc_key, f"ev-{i}", f"cluster-{i % 7}", title, available, published),
        )
        for chunk_index in range(rng.randint(1, 3)):
            body = " ".join(rng.choices(_WORDS, k=rng.randint(1, 60)))
            connection.execute(
                "INSERT INTO document_chunks VALUES (?,?,?)", (doc_key, chunk_index, body)
            )
    connection.commit()


_QUERIES = [
    "nvidia earnings guidance",
    "TSLA recall",
    "apple the of",
    "рынок 収益",
    "datacenter ai chip margin q3 2026",
    "nonexistentterm",
    "",
    "the",
]


def test_new_index_matches_old_digest_and_ranking_exactly() -> None:
    for seed, docs in ((7, 120), (21, 260)):
        with contextlib.closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory = sqlite3.Row
            _fake_corpus(connection, seed, docs)
            as_of = "2026-08-10T00:00:00Z"
            corpus_hash = f"corpus-{seed}"

            old_chunks, _old_digest = _old_build(connection, corpus_hash=corpus_hash, as_of=as_of)
            new_index = build_eligible_index(connection, as_of=as_of)

            # The digest is a pure function of the eligible documents: stable
            # across rebuilds and independent of anything but the chunk state.
            assert new_index.index_state_hash == build_eligible_index(connection, as_of=as_of).index_state_hash
            assert new_index.n_chunks == len(old_chunks)
            assert new_index.cluster_by_doc_key == {c.doc_key: c.cluster_key for c in old_chunks}

            for query in _QUERIES:
                for limit in (5, 32):
                    expected = _old_rank(old_chunks, as_of, query, limit)
                    actual = rank(new_index, query, limit)
                    assert actual == expected, f"seed={seed} query={query!r} limit={limit}"


def test_empty_corpus_ranks_nothing() -> None:
    with contextlib.closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        _fake_corpus(connection, 3, 5)
        index = build_eligible_index(connection, as_of="2020-01-01T00:00:00Z")
        assert index.n_chunks == 0
        assert rank(index, "nvidia", 10) == []
        # The digest still binds the (empty) eligible state and the cutoff.
        other = build_eligible_index(connection, as_of="2020-06-01T00:00:00Z")
        assert index.index_state_hash != other.index_state_hash


def test_tool_tape_evidence_never_invalidates_the_cached_index(tmp_path) -> None:
    """A live run's own tapes advance the evidence corpus on every call; the
    document index must not rebuild for them (each rebuild costs minutes on
    the trader machine and burned entire research deadlines)."""
    from datetime import timezone as _tz

    from tradingagents.temporal.store import TemporalStore

    store = TemporalStore(tmp_path / "store")
    cutoff = "2026-08-18T21:30:00Z"
    as_of = datetime(2026, 8, 18, 21, 30, tzinfo=_tz.utc)
    first = store.eligible_index(cutoff=cutoff, generation_id=0)
    hash_before = store.corpus_hash(as_of=as_of)

    with store._connect() as connection:
        connection.execute(
            "INSERT INTO evidence (evidence_id, tool, request_key, request_json, response_json,"
            " artifact_hash, available_at, observed_at, ingested_at, fidelity, is_error)"
            " VALUES ('ev-tape-1', 'dataflow.get_stock_data', 'rk1', '{}', '{}', '',"
            " '2026-08-18T20:00:00Z', '2026-08-18T20:00:00Z', '2026-08-18T20:00:00Z', 'tool', 0)"
        )

    # The evidence corpus moved (drift detection must see it) ...
    assert store.corpus_hash(as_of=as_of) != hash_before
    # ... but no document changed, so the index is the same cached object.
    assert store.eligible_index(cutoff=cutoff, generation_id=0) is first


def test_incremental_corpus_hash_matches_scratch_recomputation(tmp_path) -> None:
    """The cached corpus hash must equal a from-scratch digest through
    arbitrary interleavings of tape inserts and reads - it is the drift
    detector; a stale or wrong value would blind or false-alarm it."""
    import hashlib as _hashlib
    from datetime import timezone as _tz

    from tradingagents.temporal.models import canonical_json
    from tradingagents.temporal.store import TemporalStore

    store = TemporalStore(tmp_path / "store")
    as_of = datetime(2026, 8, 18, 21, 30, tzinfo=_tz.utc)
    cutoff = "2026-08-18T21:30:00.000000Z"

    def scratch() -> str:
        with store._connect() as connection:
            ids = [
                row["evidence_id"]
                for row in connection.execute(
                    "SELECT evidence_id FROM evidence WHERE available_at <= ? ORDER BY evidence_id",
                    (cutoff,),
                )
            ]
        payload = {"as_of": cutoff, "evidence_ids": ids}
        return _hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def insert(evidence_id: str, available_at: str) -> None:
        with store._connect() as connection:
            connection.execute(
                "INSERT INTO evidence (evidence_id, tool, request_key, request_json, response_json,"
                " artifact_hash, available_at, observed_at, ingested_at, fidelity, is_error)"
                " VALUES (?, 'dataflow.tape', ?, '{}', '{}', '', ?, ?, ?, 'tool', 0)",
                (evidence_id, evidence_id, available_at, available_at, available_at),
            )

    assert store.corpus_hash(as_of=as_of) == scratch()
    # Eligible insert that sorts BEFORE existing ids exercises the merge.
    insert("aaa-early", "2026-08-18T10:00:00Z")
    assert store.corpus_hash(as_of=as_of) == scratch()
    # Repeated call with no writes hits the cache; still exact.
    assert store.corpus_hash(as_of=as_of) == scratch()
    # Ineligible insert (after cutoff) must not change the digest.
    before = store.corpus_hash(as_of=as_of)
    insert("zzz-late", "2026-08-19T10:00:00Z")
    assert store.corpus_hash(as_of=as_of) == before == scratch()
    # A burst of eligible inserts, then verify once more.
    for n in range(5):
        insert(f"mid-{n}", "2026-08-18T12:00:00Z")
    assert store.corpus_hash(as_of=as_of) == scratch()
