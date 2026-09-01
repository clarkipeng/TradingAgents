import sqlite3

from scripts.verify_x_cycle import _read_media_rows, _store_label


def test_verifier_reads_a_sqlite_url_without_migrating(tmp_path):
    path = tmp_path / "media.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE collection_cycles (
            collection_cycle_id TEXT, status TEXT, cycle_kind TEXT, period_key TEXT
        );
        CREATE TABLE fetch_runs (
            fetch_run_id TEXT, provider TEXT, query_key TEXT, status TEXT,
            started_utc REAL, cost_units REAL, metadata_json TEXT
        );
        CREATE TABLE media_posts (external_id TEXT, body TEXT, source TEXT, fetched_utc REAL);
        INSERT INTO collection_cycles VALUES ('x', 'complete', 'x-daily', '2026-08-31');
        """
    )
    connection.commit()
    connection.close()

    cycles, runs, posts = _read_media_rows(str(path), 1788134400, 1788220800)

    assert cycles[0]["cycle_kind"] == "x-daily"
    assert runs == []
    assert posts == []
    assert _store_label("postgresql://user:secret@host/db") == "configured PostgreSQL database"
