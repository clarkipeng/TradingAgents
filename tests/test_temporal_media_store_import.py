from datetime import datetime, timezone

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_collectors.media_store import import_media_store_posts

UTC = timezone.utc


class _MediaStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False

    def history_asof(self, start, end, **kwargs):
        self.calls.append((start, end, kwargs))
        return self.rows

    def close(self):
        self.closed = True


def test_media_store_bridge_preserves_fetch_receipt_availability(tmp_path, monkeypatch):
    source = _MediaStore(
        [
            {
                "source": "x",
                "external_id": "tweet-1",
                "title": "",
                "body": "NVIDIA discussion",
                "created_utc": 1_708_531_000.0,
                "fetched_utc": 1_708_532_000.0,
                "metadata": {"author": "example"},
            },
            {"source": "x", "external_id": "bad"},
        ]
    )
    monkeypatch.setattr(
        "tradingagents.temporal_collectors.media_store.open_store",
        lambda _url: source,
    )
    temporal_store = TemporalStore(tmp_path)

    result = import_media_store_posts(
        temporal_store,
        start="2024-02-21",
        end="2024-02-22",
        media_db_url="sqlite:///poller.db",
        sources=("x",),
        tickers=("NVDA",),
    )

    assert result.requested == 2
    assert result.imported == 1
    assert result.failures == ("post-2:invalid-record",)
    assert source.closed is True
    assert source.calls == [
        ("2024-02-21", "2024-02-22", {"tickers": ["NVDA"], "sources": ["x"], "limit": 1000})
    ]
    record = temporal_store.get_evidence(result.evidence_ids[0])
    assert record.available_at == datetime.fromtimestamp(1_708_532_000.0, UTC)
    assert record.event_at == datetime.fromtimestamp(1_708_531_000.0, UTC)
    assert record.response["metadata"]["availability_basis"] == "poller-fetch-receipt"
    assert temporal_store.search("NVIDIA", as_of=record.available_at).results
