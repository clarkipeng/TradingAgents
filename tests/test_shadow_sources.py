from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pytest

from tradingagents.dataflows import shadow_sources
from tradingagents.research_protocol import (
    GLOBAL_EVENT_V2_COLLECTION_PROTOCOL_ID,
    GLOBAL_EVENT_V2_COLLECTOR_SEMANTICS_ID,
    GLOBAL_EVENT_V2_PROTOCOL_ID,
)


@pytest.mark.unit
def test_shadow_policy_is_separate_bounded_and_non_formal() -> None:
    policy = shadow_sources.SOURCE_SHADOW_V1_POLICY

    assert policy["formal_projection_allowed"] is False
    assert policy["cycle_kind"] == "source-shadow-daily"
    assert policy["maximum_dynamic_slots"] == 0
    assert policy["maximum_sequential_runtime_seconds"] <= 150
    assert policy["recovery_stale_seconds"] > policy["maximum_sequential_runtime_seconds"]
    assert len(shadow_sources.SOURCE_SHADOW_STATIC_SLOTS) == 5
    assert (
        sum(provider == "gdelt" for provider, _ in shadow_sources.SOURCE_SHADOW_STATIC_SLOTS) == 4
    )
    assert (
        sum(provider == "hacker_news" for provider, _ in shadow_sources.SOURCE_SHADOW_STATIC_SLOTS)
        == 1
    )
    assert policy["adapters"]["hacker_news"]["feed"] == "top"
    assert policy["adapters"]["hacker_news"]["maximum_limit"] <= 12
    assert policy["adapters"]["hacker_news"]["total_deadline_seconds"] <= 45
    declared_runtime = sum(
        policy["adapters"][provider][
            "total_deadline_seconds_per_slot"
            if provider == "gdelt"
            else "total_deadline_seconds"
        ]
        for provider, _query_key in shadow_sources.SOURCE_SHADOW_STATIC_SLOTS
    )
    assert declared_runtime <= policy["maximum_sequential_runtime_seconds"]
    timestamps = shadow_sources.SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_MANIFEST[
        "timestamp_semantics"
    ]
    assert "server-terminal fetch receipt" in timestamps["availability"]
    assert "capture-time measurements" in timestamps["hacker_news"]
    assert shadow_sources.SOURCE_SHADOW_V1_PROTOCOL_ID.startswith("protocol_")
    assert shadow_sources.SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID.startswith("collector_")


@pytest.mark.unit
def test_formal_experiment_identities_remain_frozen() -> None:
    assert GLOBAL_EVENT_V2_COLLECTION_PROTOCOL_ID == "protocol_b1f2c6f59e6290947cb5be0d"
    assert GLOBAL_EVENT_V2_COLLECTOR_SEMANTICS_ID == "collector_077b2fea4605a8cdb260dd4b"
    assert GLOBAL_EVENT_V2_PROTOCOL_ID == "protocol_282645475c60166a70209c6f"
    assert shadow_sources.SOURCE_SHADOW_V1_PROTOCOL_ID != (GLOBAL_EVENT_V2_COLLECTION_PROTOCOL_ID)
    assert shadow_sources.SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID != (
        GLOBAL_EVENT_V2_COLLECTOR_SEMANTICS_ID
    )


@pytest.mark.unit
def test_daily_cycle_spec_has_fixed_utc_identity_and_only_static_slots() -> None:
    now = datetime(2026, 8, 8, 23, 59, tzinfo=timezone.utc).timestamp()
    spec = shadow_sources.source_shadow_cycle_spec(now)

    assert spec["collection_cycle_id"].startswith("cycle_")
    assert spec["identity"] == {
        "schema_version": 1,
        "cycle_kind": "source-shadow-daily",
        "period_key": "2026-08-08",
        "protocol_id": shadow_sources.SOURCE_SHADOW_V1_PROTOCOL_ID,
        "collector_semantics_id": (shadow_sources.SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID),
        "expected_static_slots": [
            {"provider": provider, "query_key": query_key}
            for provider, query_key in shadow_sources.SOURCE_SHADOW_STATIC_SLOTS
        ],
        "max_dynamic_slots": 0,
    }


@pytest.mark.unit
def test_same_day_other_identity_is_exposed_without_authorizing_new_calls() -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=timezone.utc).timestamp()

    class Store:
        def collection_cycle_identities(self, cycle_kind: str, *, period_key: str):
            assert cycle_kind == "source-shadow-daily"
            assert period_key == "2026-08-08"
            return [
                {
                    "collection_cycle_id": "cycle_" + "0" * 24,
                    "protocol_id": "protocol_old",
                    "collector_semantics_id": "collector_old",
                }
            ]

    assert shadow_sources.source_shadow_cycle_resolution(Store(), now) == {
        "state": "other_identity_already_attempted",
        "spec": None,
    }
    with pytest.raises(shadow_sources.SourceShadowCycleIdentityError):
        shadow_sources.checked_source_shadow_cycle_spec(Store(), now)


@pytest.mark.unit
def test_empty_or_exact_same_day_inventory_authorizes_only_current_spec() -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=timezone.utc).timestamp()
    spec = shadow_sources.source_shadow_cycle_spec(now)

    class Store:
        rows: list[dict] = []

        def collection_cycle_identities(self, _cycle_kind: str, *, period_key: str):
            assert period_key == "2026-08-08"
            return self.rows

    store = Store()
    assert shadow_sources.checked_source_shadow_cycle_spec(store, now) == spec
    store.rows = [
        {
            "collection_cycle_id": spec["collection_cycle_id"],
            "protocol_id": spec["identity"]["protocol_id"],
            "collector_semantics_id": spec["identity"]["collector_semantics_id"],
        }
    ]
    assert shadow_sources.checked_source_shadow_cycle_spec(store, now) == spec


@pytest.mark.unit
def test_malformed_cycle_inventory_is_fatal_not_treated_as_prior_attempt() -> None:
    class Store:
        def collection_cycle_identities(self, _cycle_kind: str, *, period_key: str):
            assert period_key
            return [{"collection_cycle_id": None}]

    with pytest.raises(ValueError, match="inventory is malformed"):
        shadow_sources.source_shadow_cycle_resolution(Store(), 123.0)


@pytest.mark.unit
def test_slot_dispatch_accepts_only_policy_declared_queries() -> None:
    expected = [{"source": "gdelt"}]
    with mock.patch.object(
        shadow_sources.gdelt,
        "fetch_gdelt_articles",
        return_value=expected,
    ) as fetch:
        assert (
            shadow_sources.fetch_source_shadow_slot("gdelt", "category:technology", 123.0)
            == expected
        )
    fetch.assert_called_once_with("technology", 123.0)

    with (
        mock.patch.object(shadow_sources.gdelt, "fetch_gdelt_articles") as gdelt_fetch,
        mock.patch.object(
            shadow_sources.hacker_news,
            "fetch_hacker_news_stories",
        ) as hn_fetch,
        pytest.raises(ValueError, match="not declared"),
    ):
        shadow_sources.fetch_source_shadow_slot("gdelt", "category:AAPL", 123.0)
    gdelt_fetch.assert_not_called()
    hn_fetch.assert_not_called()
