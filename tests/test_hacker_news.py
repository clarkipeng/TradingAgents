from __future__ import annotations

import json
from unittest import mock

import pytest

from tradingagents.dataflows import hacker_news
from tradingagents.dataflows.errors import ProviderResponseError

_MEDIA_ROW_KEYS = {
    "source",
    "external_id",
    "ticker",
    "subreddit",
    "author",
    "sentiment",
    "created_utc",
    "title",
    "body",
    "fetched_utc",
    "metadata",
}
_CAPTURED = 1_786_190_500.0
_STORY = {
    "by": "author",
    "descendants": 42,
    "id": 123,
    "score": 77,
    "time": 1_786_190_400,
    "title": "A model launch covered by the community",
    "type": "story",
    "url": "https://example.com/launch?provider=value#fragment",
}


@pytest.mark.unit
def test_emits_exact_feed_manifest_and_content_vintage_story_row() -> None:
    with (
        mock.patch.object(hacker_news.time, "monotonic", return_value=100.0),
        mock.patch.object(hacker_news, "get_json", side_effect=[[123, 456], _STORY]) as request,
    ):
        manifest, story = hacker_news.fetch_hacker_news_stories("top", _CAPTURED, limit=1)

    assert set(manifest) == _MEDIA_ROW_KEYS
    assert set(story) == _MEDIA_ROW_KEYS
    assert story["source"] == "hacker_news"
    assert story["external_id"].startswith("hn_123_")
    assert story["ticker"] == "@HACKER_NEWS_TECHNOLOGY"
    assert story["metadata"]["engagement"] == {
        "feed": "top",
        "rank": 1,
        "score": 77,
        "comment_count": 42,
    }
    snapshot = json.loads(story["body"])
    assert snapshot["engagement"] == story["metadata"]["engagement"]
    assert (
        snapshot["provider_record_sha256"]
        == (story["metadata"]["raw_lineage"]["provider_record_sha256"])
    )
    feed_snapshot = json.loads(manifest["body"])
    assert feed_snapshot["ordered_item_ids"] == [123, 456]
    assert feed_snapshot["sampled_items"] == [
        {
            "rank": 1,
            "item_id": 123,
            "status": "story",
            "content_vintage_id": story["external_id"],
            "provider_record_sha256": story["metadata"]["raw_lineage"]["provider_record_sha256"],
        }
    ]
    assert manifest["metadata"]["item_counts"]["returned_items"] == 1
    assert request.call_count == 2
    assert request.call_args_list[0].kwargs == {
        "timeout": 3.0,
        "attempts": 2,
        "deadline": 145.0,
        "max_bytes": 64_000,
    }
    assert request.call_args_list[1].kwargs == {
        "timeout": 3.0,
        "attempts": 2,
        "deadline": 145.0,
        "max_bytes": 256_000,
    }


@pytest.mark.unit
def test_manifest_preserves_missing_deleted_dead_and_non_story_outcomes() -> None:
    deleted = {"id": 456, "deleted": True, "type": "story"}
    dead = {"id": 789, "dead": True, "type": "story"}
    job = {"id": 900, "type": "job"}
    with mock.patch.object(
        hacker_news,
        "get_json",
        side_effect=[[123, 456, 789, 900], None, deleted, dead, job],
    ):
        rows = hacker_news.fetch_hacker_news_stories("top", _CAPTURED, limit=4)

    assert len(rows) == 1
    outcomes = json.loads(rows[0]["body"])["sampled_items"]
    assert [item["status"] for item in outcomes] == [
        "missing",
        "deleted",
        "dead",
        "non_story",
    ]
    counts = rows[0]["metadata"]["item_counts"]
    assert counts["sampled_items"] == 4
    assert counts["returned_items"] == 0
    assert counts["missing"] == counts["deleted"] == counts["dead"] == 1
    assert counts["non_story"] == 1


@pytest.mark.unit
def test_first_party_outbound_launch_is_retained_and_labeled_not_filtered() -> None:
    first_party = {
        **_STORY,
        "title": "Introducing our new model",
        "url": "https://openai.com/news/model",
    }
    with mock.patch.object(hacker_news, "get_json", side_effect=[[123], first_party]):
        manifest, story = hacker_news.fetch_hacker_news_stories("top", _CAPTURED)

    assert story["metadata"]["outbound_looks_company_authored"] is True
    assert json.loads(story["body"])["outbound_looks_company_authored"] is True
    assert manifest["metadata"]["item_counts"]["returned_items"] == 1


@pytest.mark.unit
def test_content_vintage_identity_binds_mutable_engagement() -> None:
    revised = {**_STORY, "score": 78, "descendants": 43}
    with mock.patch.object(
        hacker_news,
        "get_json",
        side_effect=[[123], _STORY, [123], revised],
    ):
        first = hacker_news.fetch_hacker_news_stories("top", _CAPTURED)[1]
        second = hacker_news.fetch_hacker_news_stories("top", _CAPTURED)[1]

    assert first["external_id"] != second["external_id"]
    assert first["body"] != second["body"]
    assert first["metadata"]["engagement"] != second["metadata"]["engagement"]


@pytest.mark.unit
def test_limit_bounds_item_requests_and_retains_full_ordered_feed() -> None:
    second = {**_STORY, "id": 456, "title": "Second"}
    responses = [[123, 456, 789], _STORY, second]
    with mock.patch.object(hacker_news, "get_json", side_effect=responses) as request:
        rows = hacker_news.fetch_hacker_news_stories("top", _CAPTURED, limit=2)

    assert request.call_count == 3
    assert json.loads(rows[0]["body"])["ordered_item_ids"] == [123, 456, 789]
    assert [json.loads(row["body"])["rank"] for row in rows[1:]] == [1, 2]


@pytest.mark.unit
def test_non_top_feed_is_rejected_before_transport() -> None:
    with mock.patch.object(hacker_news, "get_json") as request, pytest.raises(ValueError):
        hacker_news.fetch_hacker_news_stories("best", _CAPTURED)
    request.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, [], [123, 123], [True]])
def test_invalid_feed_manifest_is_not_reported_as_empty(payload: object) -> None:
    with (
        mock.patch.object(hacker_news, "get_json", return_value=payload),
        pytest.raises(ProviderResponseError),
    ):
        hacker_news.fetch_hacker_news_stories("top", _CAPTURED)


@pytest.mark.unit
def test_mismatched_item_id_is_rejected() -> None:
    with (
        mock.patch.object(
            hacker_news,
            "get_json",
            side_effect=[[123], {**_STORY, "id": 456}],
        ),
        pytest.raises(ProviderResponseError, match="does not match"),
    ):
        hacker_news.fetch_hacker_news_stories("top", _CAPTURED)


@pytest.mark.unit
def test_output_is_deterministic_for_same_provider_snapshot() -> None:
    with mock.patch.object(
        hacker_news,
        "get_json",
        side_effect=[[123], _STORY, [123], _STORY],
    ):
        first = hacker_news.fetch_hacker_news_stories("top", _CAPTURED)
        second = hacker_news.fetch_hacker_news_stories("top", _CAPTURED)
    assert first == second
