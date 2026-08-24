import json
import sqlite3
from datetime import datetime, timezone

import pytest

from cli.temporal_retrieval_bench import run_benchmark
from tradingagents.temporal import TemporalStore
from tradingagents.temporal.documents import extract_document, stable_chunk_id, stable_doc_key

UTC = timezone.utc


def at(hour: int) -> datetime:
    return datetime(2025, 1, 2, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    ("metadata", "response", "kind"),
    [
        ({"article": {"title": "GDELT title", "url": "https://news.example/a"}}, {"text": "ignored"}, "gdelt"),
        ({"story": {"title": "HN title"}}, {"text": "HN title\n\nHN body"}, "hacker-news"),
        ({"form": "10-Q"}, {"text": "<SEC-HEADER>hidden</SEC-HEADER><DOCUMENT><TEXT>Revenue</TEXT></DOCUMENT>"}, "sec"),
        ({"wayback_url": "https://web.archive.org/x"}, {"text": "Wayback body"}, "wayback"),
    ],
)
def test_source_extractors_normalize_documents(metadata, response, kind):
    document = extract_document({
        "tool": "corpus.document", "request": {}, "response": {**response, "metadata": metadata},
        "source": "https://example.com", "available_at": at(9).isoformat(), "is_error": False,
    })
    assert document["doc_kind"] == kind
    if kind == "sec":
        assert document["body"] == "Revenue"


def test_stable_document_and_chunk_keys_are_versioned():
    doc_key = stable_doc_key("evidence-a", 0)
    assert doc_key == stable_doc_key("evidence-a", 0)
    assert doc_key != stable_doc_key("evidence-a", 1)
    assert stable_chunk_id(doc_key, 0) != stable_chunk_id(doc_key, 1)


def test_incremental_documents_deduplicate_and_reindex_idempotently(tmp_path):
    store = TemporalStore(tmp_path)
    first = store.record("corpus.document", {"url": "https://www.example.com/a?utm_source=x"}, {"text": "Same headline", "metadata": {}}, available_at=at(9), source="https://www.example.com/a")
    second = store.record("corpus.document", {"url": "https://example.com/a"}, {"text": "Same headline", "metadata": {}}, available_at=at(9), source="https://example.com/a")
    connection = sqlite3.connect(tmp_path / "temporal.sqlite3")
    try:
        assert connection.execute("select count(*) from documents").fetchone()[0] == 2
        assert connection.execute("select count(*) from document_chunks").fetchone()[0] == 2
    finally:
        connection.close()
    result = store.search("Same headline", as_of=at(10))
    assert len(result.results) == 1
    assert set(result.results[0].document.siblings) == {
        stable_doc_key(first.evidence_id, 0), stable_doc_key(second.evidence_id, 0)
    } - {result.results[0].doc_key}
    before = (tmp_path / "temporal.sqlite3").read_bytes()
    store.reindex_documents()
    after = (tmp_path / "temporal.sqlite3").read_bytes()
    assert before != after  # rebuild is an explicit derivative write
    connection = sqlite3.connect(tmp_path / "temporal.sqlite3")
    try:
        counts = tuple(connection.execute("select count(*) from " + table).fetchone()[0] for table in ("documents", "document_chunks"))
    finally:
        connection.close()
    store.reindex_documents()
    connection = sqlite3.connect(tmp_path / "temporal.sqlite3")
    try:
        assert counts == tuple(connection.execute("select count(*) from " + table).fetchone()[0] for table in ("documents", "document_chunks"))
    finally:
        connection.close()


def test_incremental_cluster_assignments_match_full_reindex(tmp_path):
    """Per-insert clustering must agree with the authoritative rebuild.

    If the incremental path assigns a new document a cluster key its
    near-duplicate partners do not share, search-time deduplication differs
    by code path and backtest results silently depend on how the corpus was
    built. Both attach-to-existing-cluster and bridge-two-clusters shapes
    must converge to the rebuild's assignment."""
    store = TemporalStore(tmp_path)
    titles = [
        "Nvidia quarterly earnings beat expectations across data center segment",
        "Nvidia quarterly earnings beat expectations across data center segments",
        "Nvidia quarterly earnings beat expectations across data center segment today",
    ]
    for index, title in enumerate(titles):
        store.record(
            "corpus.document",
            {"url": f"https://news.example/{index}"},
            {
                "text": f"{title}\n\nBody {index}",
                "metadata": {
                    "article": {
                        "title": title,
                        "url": f"https://news.example/article-{index}",
                    }
                },
            },
            available_at=at(9),
            source=f"https://news.example/{index}",
        )

    def cluster_map() -> dict:
        connection = sqlite3.connect(tmp_path / "temporal.sqlite3")
        try:
            return dict(connection.execute(
                "select doc_key, cluster_key from documents"
            ).fetchall())
        finally:
            connection.close()

    incremental = cluster_map()
    store.reindex_documents()
    rebuilt = cluster_map()
    assert len(set(rebuilt.values())) == 1  # the titles genuinely cluster
    assert incremental == rebuilt


def test_store_open_does_not_write_existing_database(tmp_path):
    store = TemporalStore(tmp_path)
    store.record("corpus.document", {"url": "https://example.com"}, {"text": "body", "metadata": {}}, available_at=at(9))
    before = (tmp_path / "temporal.sqlite3").read_bytes()
    TemporalStore(tmp_path)
    assert (tmp_path / "temporal.sqlite3").read_bytes() == before


def test_search_aggregates_large_documents_before_hydration(tmp_path):
    store = TemporalStore(tmp_path)
    body = "NVIDIA quarterly 10-Q filing supply demand " * 45_000
    store.record(
        "corpus.document",
        {"url": "https://sec.example/10-q"},
        {"text": body, "metadata": {"form": "10-Q"}},
        available_at=at(9),
    )
    result = store.search("NVIDIA quarterly 10-Q filing", as_of=at(10), limit=3)
    assert len(result.results) == 1
    assert result.results[0].doc_key


def test_benchmark_maps_evidence_targets_to_document_keys_and_rejects_unknown(tmp_path):
    database = tmp_path / "temporal.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, tool TEXT, available_at TEXT);
        CREATE TABLE documents (doc_key TEXT PRIMARY KEY, parent_evidence_id TEXT, available_at TEXT);
        CREATE TABLE document_chunks (rowid INTEGER PRIMARY KEY, doc_key TEXT, body TEXT);
        CREATE VIRTUAL TABLE document_chunks_fts USING fts5(body, content='document_chunks', content_rowid='rowid');
    """)
    connection.execute("insert into evidence values ('evidence-a','corpus.document','2025-01-02T09:00:00.000000Z')")
    connection.execute("insert into documents values ('document-a','evidence-a','2025-01-02T09:00:00.000000Z')")
    connection.execute("insert into document_chunks values (1,'document-a','alpha signal')")
    connection.execute("insert into document_chunks_fts(rowid,body) values (1,'alpha signal')")
    connection.commit()
    connection.close()
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps({"as_of": "2025-01-02T12:00:00Z", "queries": [{"query": "alpha", "expected_document_keys": ["evidence-a"]}]}))
    assert run_benchmark(bench, tmp_path)["queries"][0]["recall"] == 1
    bench.write_text(json.dumps({"queries": [{"query": "alpha", "expected_document_keys": ["missing"]}]}))
    with pytest.raises(ValueError, match="neither an evidence ID nor document key"):
        run_benchmark(bench, tmp_path)


def test_rubric_seals_with_mixed_tool_and_document_evidence(tmp_path):
    # Rubrics legitimately mix searchable documents with tool-tape evidence
    # (price data, fundamentals): the evaluator scores coverage of both, and
    # only document-backed entries concern the retrieval bench. Sealing must
    # require document keys for corpus documents only.
    store = TemporalStore(tmp_path)
    doc = store.record(
        "corpus.document",
        {"url": "one"},
        {"text": "NVDA supply constraints"},
        available_at="2025-01-02T09:00:00Z",
    )
    tool_blob = store.record(
        "dataflow.get_stock_data",
        {"args": ["NVDA"], "kwargs": {}},
        "csv payload",
        available_at="2025-01-02T09:00:00Z",
    )
    store.seal_scenario("mixed", as_of="2025-01-02T10:00:00Z", basis="forward-captured")

    rubric = store.seal_scenario_rubric(
        "mixed",
        material_evidence_ids=(doc.evidence_id, tool_blob.evidence_id),
        useful_evidence_ids=(doc.evidence_id, tool_blob.evidence_id),
    )

    assert set(rubric.material_evidence_ids) == {doc.evidence_id, tool_blob.evidence_id}
    stored = store.get_scenario_rubric("mixed")
    assert set(stored.useful_evidence_ids) == {doc.evidence_id, tool_blob.evidence_id}


def test_rubric_still_requires_reindex_for_unmapped_documents(tmp_path):
    store = TemporalStore(tmp_path)
    doc = store.record(
        "corpus.document",
        {"url": "one"},
        {"text": "NVDA supply constraints"},
        available_at="2025-01-02T09:00:00Z",
    )
    store.seal_scenario("unmapped", as_of="2025-01-02T10:00:00Z", basis="forward-captured")
    with store._connect() as connection:
        connection.execute("DELETE FROM document_chunks_fts")
        connection.execute("DELETE FROM document_chunks")
        connection.execute("DELETE FROM documents")

    with pytest.raises(ValueError, match="reindexed"):
        store.seal_scenario_rubric(
            "unmapped",
            material_evidence_ids=(doc.evidence_id,),
            useful_evidence_ids=(doc.evidence_id,),
        )
