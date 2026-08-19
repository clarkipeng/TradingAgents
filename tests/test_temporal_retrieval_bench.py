import json

from cli.temporal_retrieval_bench import run_benchmark


def test_synthetic_benchmark_meets_metric_floor(tmp_path):
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps({
        "as_of": "2025-01-02T12:00:00Z",
        "documents": [
            {"document_key": "a", "available_at": "2025-01-02T09:00:00Z", "text": "alpha signal"},
            {"document_key": "b", "available_at": "2025-01-02T09:00:00Z", "text": "beta signal"},
        ],
        "queries": [{"query": "alpha", "expected_document_keys": ["a"], "kind": "known-item"}],
        "floors": {"success@k": 1, "recall@k": 1, "nDCG@k": 1, "MRR": 1},
    }))
    result = run_benchmark(bench, k=2)
    assert result["floor_failures"] == []
    assert result["queries"][0]["misses"] == []


def test_real_store_benchmark_does_not_mutate_database(tmp_path):
    database = tmp_path / "temporal.sqlite3"
    import sqlite3

    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, available_at TEXT NOT NULL);"
        "CREATE VIRTUAL TABLE evidence_fts USING fts5(evidence_id UNINDEXED, content);"
        "CREATE TABLE search_traces (trace_id TEXT, query TEXT, as_of TEXT, evidence_ids_json TEXT);"
    )
    connection.execute("INSERT INTO evidence VALUES ('doc-a', '2025-01-02T09:00:00.000000Z')")
    connection.execute("INSERT INTO evidence_fts VALUES ('doc-a', 'alpha signal')")
    connection.execute("INSERT INTO search_traces VALUES ('t', 'alpha', '2025-01-02T12:00:00Z', '[\"doc-a\"]')")
    connection.commit()
    connection.close()
    before = database.read_bytes()
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps({"topic_from_search_traces": {"limit": 1}}))
    result = run_benchmark(bench, tmp_path)
    assert result["queries"][0]["recall"] == 1
    assert database.read_bytes() == before
