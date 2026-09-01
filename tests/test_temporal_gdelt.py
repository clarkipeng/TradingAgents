from datetime import datetime, timezone

import pytest

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_collectors.gdelt import (
    GdeltResponseError,
    import_gdelt_articles,
)

UTC = timezone.utc


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.payload)


def test_gdelt_requests_are_paced_across_calls(tmp_path, monkeypatch):
    """GDELT's DOC API allows one request every 5 seconds; the daily sweep
    makes ~60, so the pace must hold across import calls by construction."""
    from tradingagents.dataflows import gdelt_common

    clock = {"now": 500.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(gdelt_common.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(gdelt_common.time, "sleep", fake_sleep)
    monkeypatch.setattr(gdelt_common, "_gdelt_last_request_at", [float("-inf")])

    store = TemporalStore(tmp_path)
    session = _Session({"articles": []})
    import_gdelt_articles(
        store, query="NVDA", start="2024-02-01", end="2024-02-01", session=session
    )
    assert sleeps == []  # first request never waits
    import_gdelt_articles(
        store, query="NVDA", start="2024-02-02", end="2024-02-02", session=session
    )
    assert sleeps == [
        pytest.approx(gdelt_common.GDELT_MIN_REQUEST_INTERVAL_SECONDS)
    ]
    assert len(session.calls) == 2


def test_gdelt_retries_escalate_their_wait_to_outlast_429_bursts(monkeypatch):
    """GDELT serves 429 bursts even at a compliant cadence; retrying inside
    the same burst just burns attempts. Each retry pays a growing extra wait
    and re-enters the shared pace gate."""
    from tradingagents.dataflows import gdelt_common
    from tradingagents.dataflows.errors import ProviderTransientError
    from tradingagents.temporal_collectors import gdelt as gdelt_module

    monkeypatch.setattr(gdelt_common, "_gdelt_last_request_at", [float("-inf")])
    paces = []
    monkeypatch.setattr(
        gdelt_module, "pace_gdelt_request", lambda: paces.append(True)
    )
    extra_sleeps = []
    attempts = []

    def flaky_get_json(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 3:
            raise ProviderTransientError("burst")
        return {"articles": []}

    monkeypatch.setattr(gdelt_module, "get_json", flaky_get_json)

    payload = gdelt_module._paced_doc_request("https://example.invalid", sleep=extra_sleeps.append)

    assert payload == {"articles": []}
    assert len(attempts) == 3
    assert len(paces) == 3  # every attempt re-enters the pace gate
    assert extra_sleeps == list(gdelt_module._GDELT_RETRY_EXTRA_WAITS_SECONDS[1:])

    dead_attempts = []

    def dead_get_json(url, **kwargs):
        dead_attempts.append(url)
        raise ProviderTransientError("still down")

    monkeypatch.setattr(gdelt_module, "get_json", dead_get_json)
    with pytest.raises(ProviderTransientError):
        gdelt_module._paced_doc_request(
            "https://always.invalid", sleep=extra_sleeps.append
        )
    assert len(dead_attempts) == 3  # hard-bounded; a dead provider fails the query


def test_gdelt_import_preserves_query_artifact_and_uses_seen_clock(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session(
        {
            "articles": [
                {
                    "url": "https://news.example/nvda",
                    "title": "NVIDIA data-center demand rises",
                    "seendate": "20240221T170203Z",
                    "domain": "news.example",
                    "language": "English",
                },
                {"url": "https://news.example/bad", "title": "Missing timestamp"},
            ]
        }
    )

    before_fetch = datetime.now(UTC)
    result = import_gdelt_articles(
        store,
        query="NVDA",
        start="2024-02-21",
        end="2024-02-21",
        session=session,
    )

    assert result.requested == 2
    assert result.imported == 1
    assert result.failures == ("article-2:missing-url-title-or-seendate",)
    record = store.get_evidence(result.evidence_ids[0])
    after_fetch = datetime.now(UTC)
    assert before_fetch <= record.available_at <= after_fetch
    assert record.observed_at == record.available_at
    assert record.source == "https://news.example/nvda"
    metadata = record.response["metadata"]
    assert metadata["available_at_policy"] == "fetch-receipt"
    assert metadata["availability_basis"] == "gdelt-fetch-receipt"
    assert metadata["provider_available_at_estimate"] == "2024-02-21T17:02:03+00:00"
    assert "seendate" not in record.request
    assert "seendate" not in metadata["article"]
    assert record.response["metadata"]["original_content"] == "not-fetched"
    assert store.read_artifact(result.response_artifact_hash)
    params = session.calls[0][1]["params"]
    assert params["startdatetime"] == "20240221000000"
    assert params["enddatetime"] == "20240221235959"


def test_gdelt_import_rejects_unusable_rate_limit_bodies_and_invalid_ranges(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session(ValueError("not JSON"))

    with pytest.raises(GdeltResponseError, match="non-JSON"):
        import_gdelt_articles(
            store,
            query="NVDA",
            start="2024-02-21",
            end="2024-02-21",
            session=session,
        )
    with pytest.raises(ValueError, match="start must not be after end"):
        import_gdelt_articles(
            store,
            query="NVDA",
            start="2024-02-22",
            end="2024-02-21",
            session=session,
        )


def test_gdelt_archive_import_uses_the_shared_bounded_transport_and_query_normalization(
    tmp_path, monkeypatch
):
    captured = {}

    def fake_get_json(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return {
            "articles": [
                {
                    "url": "https://news.example/nvda",
                    "title": "NVIDIA data-center demand rises",
                    "seendate": "20240221T170203Z",
                }
            ]
        }

    monkeypatch.setattr("tradingagents.temporal_collectors.gdelt.get_json", fake_get_json)
    result = import_gdelt_articles(
        TemporalStore(tmp_path),
        query="  NVDA   OR   AI  ",
        start="2024-02-21",
        end="2024-02-21",
    )

    assert result.imported == 1
    assert "query=NVDA+OR+AI" in captured["url"]
    assert captured["attempts"] == 1
    assert captured["max_bytes"] == 1_000_000
