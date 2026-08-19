from datetime import datetime, timezone

from tradingagents.temporal import TemporalStore, import_archive_jsonl

UTC = timezone.utc


def at(hour: int) -> datetime:
    return datetime(2025, 1, 2, hour, tzinfo=UTC)


def test_temporal_search_excludes_future_evidence(tmp_path):
    store = TemporalStore(tmp_path / "store")
    store.record("corpus.document", {"url": "one"}, {"text": "NVDA supply constraints"}, available_at=at(9))
    store.record("corpus.document", {"url": "two"}, {"text": "NVDA supply improves"}, available_at=at(11))

    early = store.search("NVDA supply", as_of=at(10))
    later = store.search("NVDA supply", as_of=at(12))

    assert len(early.results) == 1
    assert len(later.results) == 2
    assert early.results[0].evidence.response["text"] == "NVDA supply constraints"
    assert early.manifest.ranker_version == "sqlite-fts5-v1"
    assert early.manifest.corpus_hash != later.manifest.corpus_hash


def test_archive_jsonl_is_explicitly_reconstructed_and_searchable(tmp_path):
    archive = tmp_path / "archive.jsonl"
    archive.write_text(
        '{"source_url":"https://example.com/filing","event_at":"2025-01-02T08:00:00Z",'
        '"source_published_at":"2025-01-02T08:30:00Z","available_at":"2025-01-02T09:00:00Z",'
        '"document":{"title":"Earnings","text":"NVDA reported record revenue"}}\n',
        encoding="utf-8",
    )
    store = TemporalStore(tmp_path / "store")

    summary = import_archive_jsonl(archive, store)
    result = store.search("record revenue", as_of=at(10))

    assert summary.imported == 1
    assert result.results[0].evidence.fidelity == "archive-reconstructed"
    assert result.results[0].evidence.source == "https://example.com/filing"
    assert result.results[0].evidence.event_at == at(8)
    assert result.results[0].evidence.source_published_at == datetime(2025, 1, 2, 8, 30, tzinfo=UTC)
