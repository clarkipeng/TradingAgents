from datetime import datetime, timezone

import pytest

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_collectors.reddit_archive import import_reddit_archive

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
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(next(self.payloads))


def test_reddit_archive_imports_bounded_posts_and_comments(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session(
        [
            {
                "data": [
                    {
                        "id": "post-1",
                        "subreddit": "stocks",
                        "title": "NVIDIA earnings",
                        "selftext": "NVDA looks strong",
                        "created_utc": 1_708_531_723,
                        "permalink": "/r/stocks/comments/post-1/nvidia/",
                    }
                ]
            },
            {
                "data": [
                    {
                        "id": "comment-1",
                        "subreddit": "stocks",
                        "body": "NVDA demand is real",
                        "created_utc": 1_708_531_800,
                    }
                ]
            },
        ]
    )

    result = import_reddit_archive(
        store,
        ticker="NVDA",
        start="2024-02-21",
        end="2024-02-21",
        subreddits=("stocks",),
        session=session,
    )

    assert result.requested == 2
    assert result.imported == 2
    assert not result.failures
    assert len(result.response_artifact_hashes) == 2
    record = store.get_evidence(result.evidence_ids[0])
    assert record.available_at == datetime.fromtimestamp(1_708_531_723, UTC)
    assert record.response["metadata"]["availability_basis"] == "reddit-created_utc"
    assert store.search("demand", as_of="2024-02-22T00:00:00Z").results
    first_params = session.calls[0][1]["params"]
    second_params = session.calls[1][1]["params"]
    assert first_params["query"] == "NVDA"
    assert second_params["body"] == "NVDA"
    assert first_params["before"] == "2024-02-22"


def test_reddit_archive_rejects_provider_error_and_invalid_range(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session([{"data": None, "error": "Timeout"}, {"data": []}])

    result = import_reddit_archive(
        store,
        ticker="NVDA",
        start="2024-02-21",
        end="2024-02-21",
        subreddits=("stocks",),
        session=session,
    )
    assert result.imported == 0
    assert result.failures == ("stocks:post:RedditArchiveResponseError",)
    with pytest.raises(ValueError, match="start must not be after end"):
        import_reddit_archive(
            store,
            ticker="NVDA",
            start="2024-02-22",
            end="2024-02-21",
            subreddits=("stocks",),
            session=_Session([]),
        )
