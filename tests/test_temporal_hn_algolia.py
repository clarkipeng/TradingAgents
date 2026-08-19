from datetime import datetime, timezone

import pytest

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_collectors.hn_algolia import (
    HackerNewsArchiveResponseError,
    import_hacker_news_stories,
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


def test_hn_algolia_import_preserves_raw_search_and_story_clock(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session(
        {
            "hits": [
                {
                    "objectID": "123",
                    "title": "NVIDIA launches a new platform",
                    "story_text": "Discussion summary",
                    "created_at_i": 1_708_531_723,
                },
                {"objectID": "bad", "title": "Missing clock"},
            ]
        }
    )

    result = import_hacker_news_stories(
        store,
        query="NVIDIA",
        start="2024-02-21",
        end="2024-02-21",
        session=session,
    )

    assert result.requested == 2
    assert result.imported == 1
    assert result.failures == ("story-2:invalid-record",)
    record = store.get_evidence(result.evidence_ids[0])
    assert record.available_at == datetime.fromtimestamp(1_708_531_723, UTC)
    assert record.source == "https://news.ycombinator.com/item?id=123"
    assert record.response["metadata"]["availability_basis"] == "hn-created_at_i"
    assert store.read_artifact(result.response_artifact_hash)
    params = session.calls[0][1]["params"]
    assert params["tags"] == "story"
    assert params["numericFilters"] == "created_at_i>=1708473600,created_at_i<=1708559999"


def test_hn_algolia_import_rejects_non_json_and_invalid_ranges(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session(ValueError("not JSON"))

    with pytest.raises(HackerNewsArchiveResponseError, match="non-JSON"):
        import_hacker_news_stories(
            store,
            query="NVIDIA",
            start="2024-02-21",
            end="2024-02-21",
            session=session,
        )
    with pytest.raises(ValueError, match="start must not be after end"):
        import_hacker_news_stories(
            store,
            query="NVIDIA",
            start="2024-02-22",
            end="2024-02-21",
            session=session,
        )
