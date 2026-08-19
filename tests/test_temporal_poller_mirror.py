from datetime import datetime, timezone

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_adapters.poller import mirror_poller_media_fetch

UTC = timezone.utc


def test_poller_mirror_is_opt_in_and_uses_terminal_receipt_clock(tmp_path, monkeypatch):
    rows = [
        {
            "source": "x",
            "external_id": "tweet-1",
            "title": "",
            "body": "NVIDIA discussion on X",
            "created_utc": 1_708_531_000.0,
            "fetched_utc": 1_708_532_000.0,
        }
    ]
    assert mirror_poller_media_fetch(
        rows,
        provider="x",
        query_key="chip-demand",
        fetch_run_id="fetch-1",
        received_utc=1_708_532_500.0,
    ) == 0

    monkeypatch.setenv("TRADINGAGENTS_POLLER_TEMPORAL_STORE", str(tmp_path))
    imported = mirror_poller_media_fetch(
        rows,
        provider="x",
        query_key="chip-demand",
        fetch_run_id="fetch-1",
        received_utc=1_708_532_500.0,
    )

    assert imported == 1
    store = TemporalStore(tmp_path)
    result = store.search("NVIDIA", as_of=datetime.fromtimestamp(1_708_532_500.0, UTC))
    assert len(result.results) == 1
    record = result.results[0].evidence
    assert record.available_at == datetime.fromtimestamp(1_708_532_500.0, UTC)
    assert record.event_at == datetime.fromtimestamp(1_708_531_000.0, UTC)
    assert record.response["metadata"]["poller_fetch_run_id"] == "fetch-1"


def test_poller_mirror_skips_non_post_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_POLLER_TEMPORAL_STORE", str(tmp_path))

    assert mirror_poller_media_fetch(
        [{"source": "hacker_news", "external_id": "manifest"}],
        provider="hacker_news",
        query_key="feed:top",
        fetch_run_id="fetch-2",
        received_utc=1_708_532_500.0,
    ) == 0
