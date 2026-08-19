from __future__ import annotations

import hashlib
import json

import pytest

from tradingagents import global_research, poller
from tradingagents.dataflows.media_store import SqliteMediaStore
from tradingagents.dataflows.shadow_sources import (
    SOURCE_SHADOW_STATIC_SLOTS,
    SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID,
    SOURCE_SHADOW_V1_PROTOCOL_ID,
    source_shadow_cycle_spec,
)


def _shadow_row(provider: str, query_key: str, captured: float) -> dict:
    external_id = hashlib.sha256(
        f"{provider}\0{query_key}\0{captured}".encode()
    ).hexdigest()[:24]
    return {
        "source": provider,
        "external_id": f"shadow_{external_id}",
        "ticker": f"@{provider}",
        "subreddit": None,
        "author": None,
        "sentiment": None,
        "created_utc": captured - 1,
        "title": f"{provider} broad discovery",
        "body": json.dumps(
            {"provider": provider, "query_key": query_key},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "fetched_utc": captured,
        "metadata": {"evidence_role": "shadow_topic_discovery"},
    }


@pytest.mark.unit
def test_source_shadow_cycle_is_exactly_once_content_bound_and_non_formal(
    tmp_path, monkeypatch,
) -> None:
    store = SqliteMediaStore(tmp_path / "source-shadow.db")
    now = store.server_observed_utc()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(poller.time, "time", lambda: now)

    def fetch(provider: str, query_key: str, captured: float) -> list[dict]:
        calls.append((provider, query_key))
        return [_shadow_row(provider, query_key, captured)]

    monkeypatch.setattr(poller, "fetch_source_shadow_slot", fetch)
    assert poller.poll_source_shadow_once(store, now) == list(
        SOURCE_SHADOW_STATIC_SLOTS
    )

    spec = source_shadow_cycle_spec(now)
    cycle = store.collection_cycle(spec["collection_cycle_id"])
    receipts = [
        receipt
        for receipt in store.fetch_runs(limit=100)
        if receipt["collection_cycle_id"] == spec["collection_cycle_id"]
    ]
    assert cycle["status"] == "complete"
    assert calls == list(SOURCE_SHADOW_STATIC_SLOTS)
    assert len(receipts) == len(SOURCE_SHADOW_STATIC_SLOTS)
    for receipt in receipts:
        metadata = json.loads(receipt["metadata_json"])
        assert metadata["protocol_id"] == SOURCE_SHADOW_V1_PROTOCOL_ID
        assert (
            metadata["collector_semantics_id"]
            == SOURCE_SHADOW_V1_COLLECTOR_SEMANTICS_ID
        )
        assert metadata["formal_effect"] == "none"
        assert receipt["formal_eligible_item_count"] is None
    for provider, query_key in SOURCE_SHADOW_STATIC_SLOTS:
        assert not global_research.is_formally_eligible_evidence(
            _shadow_row(provider, query_key, now),
            as_of_utc=now + 1,
        )

    monkeypatch.setattr(
        poller,
        "fetch_source_shadow_slot",
        lambda *_args, **_kwargs: pytest.fail("terminal shadow cycle was retried"),
    )
    assert poller.poll_source_shadow_once(store, now) == list(
        SOURCE_SHADOW_STATIC_SLOTS
    )
    assert len(store.fetch_runs(limit=100)) == len(receipts)
    store.close()


@pytest.mark.unit
def test_source_provider_failures_are_terminal_but_non_throwing(
    tmp_path, monkeypatch,
) -> None:
    store = SqliteMediaStore(tmp_path / "source-shadow-failure.db")
    now = store.server_observed_utc()
    monkeypatch.setattr(poller.time, "time", lambda: now)
    monkeypatch.setattr(
        poller,
        "fetch_source_shadow_slot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            poller.ProviderTransientError("unavailable")
        ),
    )

    poller.poll_source_shadow_once(store, now)

    spec = source_shadow_cycle_spec(now)
    cycle = store.collection_cycle(spec["collection_cycle_id"])
    receipts = [
        receipt
        for receipt in store.fetch_runs(limit=100)
        if receipt["collection_cycle_id"] == spec["collection_cycle_id"]
    ]
    assert cycle["status"] == "incomplete"
    assert len(receipts) == len(SOURCE_SHADOW_STATIC_SLOTS)
    assert {receipt["status"] for receipt in receipts} == {"failed"}
    projection = poller._shadow_cycle_audit_projection(store, spec)
    assert projection["state"] == "incomplete"
    assert projection["requests"] == len(SOURCE_SHADOW_STATIC_SLOTS)
    assert projection["items"] == 0
    store.close()


@pytest.mark.unit
def test_source_shadow_rechecks_identity_after_creating_cycle(
    tmp_path, monkeypatch,
) -> None:
    store = SqliteMediaStore(tmp_path / "source-shadow-policy-race.db")
    now = store.server_observed_utc()
    spec = source_shadow_cycle_spec(now)
    resolutions = iter([
        {"state": "ready", "spec": spec},
        {"state": "other_identity_already_attempted", "spec": None},
    ])
    monkeypatch.setattr(
        poller,
        "source_shadow_cycle_resolution",
        lambda *_args: next(resolutions),
    )
    monkeypatch.setattr(
        poller,
        "fetch_source_shadow_slot",
        lambda *_args: pytest.fail("policy race reached a provider"),
    )

    assert poller.poll_source_shadow_once(store, now) == []
    assert store.collection_cycle(spec["collection_cycle_id"])["status"] == "incomplete"
    assert store.fetch_runs(limit=10) == []
    store.close()


@pytest.mark.unit
def test_daemon_publishes_core_health_before_source_shadow(monkeypatch) -> None:
    events: list[str] = []
    stop = {"flag": False}

    class Store:
        def server_observed_utc(self) -> float:
            events.append("clock")
            return 1_800_000_000.0

    class Health:
        def mark_cycle(self, _coverage: dict, *, completed_utc: float) -> None:
            assert completed_utc > 0
            events.append("health")

    def core(*_args, **_kwargs) -> dict:
        events.append("core")
        return {"periodic_requirements": {}}

    def source_shadow(_store, observed_utc: float) -> None:
        assert observed_utc == 1_800_000_000.0
        events.append("source_shadow")

    monkeypatch.setattr(poller, "run_cycle", core)
    monkeypatch.setattr(
        poller,
        "poll_source_shadow_once",
        source_shadow,
    )
    monkeypatch.setattr(
        poller,
        "_sleep",
        lambda _seconds, state, **_kwargs: state.__setitem__("flag", True),
    )
    poller.poll_forever(
        Store(),
        [],
        [],
        3600,
        {},
        source_shadow_enabled=True,
        health_state=Health(),
        stop=stop,
        on_cycle_terminal=lambda: events.append("terminal"),
    )
    assert events == ["core", "health", "terminal", "clock", "source_shadow"]
