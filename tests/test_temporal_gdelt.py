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
    assert record.available_at == datetime(2024, 2, 21, 17, 2, 3, tzinfo=UTC)
    assert record.source == "https://news.example/nvda"
    assert record.response["metadata"]["availability_basis"] == "gdelt-seendate"
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
