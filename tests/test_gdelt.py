from __future__ import annotations

import json
from unittest import mock

import pytest

from tradingagents.dataflows import gdelt
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
_CAPTURED = 1_786_190_400.0
_ARTICLE = {
    "url": "https://www.example.com/story?provider=value#section",
    "title": "A global technology story",
    "seendate": "20260808T120000Z",
    "domain": "www.example.com",
    "language": "English",
    "sourcecountry": "United States",
    "tone": "9.99,0,0",
}


@pytest.mark.unit
def test_emits_manifest_and_exact_content_vintage_media_row() -> None:
    with mock.patch.object(gdelt, "get_json", return_value={"articles": [_ARTICLE]}):
        manifest, article = gdelt.fetch_gdelt_articles("technology", _CAPTURED)

    assert set(manifest) == _MEDIA_ROW_KEYS
    assert set(article) == _MEDIA_ROW_KEYS
    assert manifest["metadata"]["item_counts"] == {
        "provider_items": 1,
        "filtered_company_authored": 0,
        "duplicates_collapsed": 0,
        "returned_items": 1,
    }
    assert article["source"] == "gdelt"
    assert article["external_id"].startswith("gdelt_")
    assert article["ticker"] == "@GDELT_TECHNOLOGY"
    assert article["created_utc"] == _CAPTURED
    assert article["author"] == "example.com"
    assert article["metadata"]["provider_external_id"] == (
        "https://www.example.com/story?provider=value"
    )
    assert article["metadata"]["content_vintage_id"] == article["external_id"]
    assert article["metadata"]["publisher_domain"] == "example.com"
    assert len(article["metadata"]["raw_lineage"]["provider_record_sha256"]) == 64
    snapshot = json.loads(article["body"])
    assert snapshot["provider_rank"] == 1
    assert snapshot["source_observed_utc"] == _CAPTURED
    rendered = repr([manifest, article]).lower()
    assert "tone" not in rendered
    assert "sentiment ground truth" not in rendered


@pytest.mark.unit
def test_request_uses_exact_whole_second_utc_boundaries_not_relative_timespan() -> None:
    with (
        mock.patch.object(gdelt.time, "monotonic", return_value=100.0),
        mock.patch.object(gdelt, "get_json", return_value={"articles": []}) as request,
    ):
        rows = gdelt.fetch_gdelt_articles(
            "global_affairs",
            _CAPTURED + 0.75,
            limit=7,
            lookback_hours=12,
            timeout=3.0,
        )

    assert len(rows) == 1
    url = request.call_args.args[0]
    assert "STARTDATETIME=20260808000000" in url
    assert "ENDDATETIME=20260808120000" in url
    assert "maxrecords=7" in url
    assert "timespan" not in url.lower()
    assert request.call_args.kwargs == {
        "timeout": 3.0,
        "attempts": 2,
        "max_bytes": 1_000_000,
        "deadline": 110.0,
    }
    assert rows[0]["metadata"]["request_window"] == {
        "start_utc": _CAPTURED - 12 * 3600,
        "end_utc": _CAPTURED,
        "lookback_semantics": "explicit-inclusive-UTC-window",
    }


@pytest.mark.unit
def test_shared_company_authorship_filter_excludes_first_party_story() -> None:
    first_party = {
        **_ARTICLE,
        "url": "https://openai.com/news/model",
        "domain": "openai.com",
        "title": "Introducing our new model",
    }
    with mock.patch.object(gdelt, "get_json", return_value={"articles": [first_party]}):
        rows = gdelt.fetch_gdelt_articles("technology", _CAPTURED)

    assert len(rows) == 1
    assert rows[0]["metadata"]["item_counts"]["filtered_company_authored"] == 1
    assert rows[0]["metadata"]["item_counts"]["returned_items"] == 0
    assert "Introducing our new model" not in repr(rows)


@pytest.mark.unit
def test_content_vintage_identity_binds_rank_and_normalized_snapshot() -> None:
    second = {**_ARTICLE, "title": "A revised global technology story"}
    with mock.patch.object(
        gdelt,
        "get_json",
        side_effect=[{"articles": [_ARTICLE]}, {"articles": [second]}],
    ):
        first_row = gdelt.fetch_gdelt_articles("technology", _CAPTURED)[1]
        revised_row = gdelt.fetch_gdelt_articles("technology", _CAPTURED)[1]

    assert first_row["external_id"] != revised_row["external_id"]
    assert first_row["body"] != revised_row["body"]


@pytest.mark.unit
def test_unknown_category_is_rejected_before_transport() -> None:
    with mock.patch.object(gdelt, "get_json") as request, pytest.raises(ValueError):
        gdelt.fetch_gdelt_articles("AAPL", _CAPTURED)
    request.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [{}, {"articles": "not-a-list"}, {"articles": [{"title": "incomplete"}]}],
)
def test_malformed_payload_is_not_reported_as_empty(payload: object) -> None:
    with (
        mock.patch.object(gdelt, "get_json", return_value=payload),
        pytest.raises(ProviderResponseError),
    ):
        gdelt.fetch_gdelt_articles("technology", _CAPTURED)


@pytest.mark.unit
def test_provider_item_outside_explicit_window_is_rejected() -> None:
    stale = {**_ARTICLE, "seendate": "20260801T120000Z"}
    with (
        mock.patch.object(gdelt, "get_json", return_value={"articles": [stale]}),
        pytest.raises(ProviderResponseError, match="outside"),
    ):
        gdelt.fetch_gdelt_articles("technology", _CAPTURED)


@pytest.mark.unit
def test_output_is_deterministic_for_same_provider_snapshot() -> None:
    payload = {"articles": [_ARTICLE]}
    with mock.patch.object(gdelt, "get_json", return_value=payload):
        first = gdelt.fetch_gdelt_articles("technology", _CAPTURED)
        second = gdelt.fetch_gdelt_articles("technology", _CAPTURED)
    assert first == second
