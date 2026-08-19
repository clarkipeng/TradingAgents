from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.temporal import (
    ReplayedToolError,
    ReplayMissError,
    TemporalContext,
    TemporalGateway,
    TemporalMode,
    TemporalStore,
    canonical_json,
    current_context,
    temporal_context,
)

UTC = timezone.utc


def at(hour: int) -> datetime:
    return datetime(2025, 1, 2, hour, tzinfo=UTC)


def test_canonical_json_is_stable_for_equivalent_requests():
    assert canonical_json({"ticker": "NVDA", "filters": {"days": 7, "source": "news"}}) == canonical_json(
        {"filters": {"source": "news", "days": 7}, "ticker": "NVDA"}
    )


def test_store_deduplicates_artifacts_and_selects_latest_eligible(tmp_path):
    store = TemporalStore(tmp_path)
    request = {"ticker": "NVDA", "start": "2025-01-01", "end": "2025-01-02"}
    first = store.record("news", request, {"headline": "first"}, available_at=at(9))
    second = store.record("news", request, {"headline": "second"}, available_at=at(10))
    duplicate = store.record("other", {"ticker": "NVDA"}, {"headline": "second"}, available_at=at(11))

    assert store.read_artifact(second.artifact_hash) == store.read_artifact(duplicate.artifact_hash)
    assert store.latest_eligible("news", request, as_of=at(8)) is None
    assert store.latest_eligible("news", request, as_of=at(9)).evidence_id == first.evidence_id
    assert store.latest_eligible("news", request, as_of=at(10)).evidence_id == second.evidence_id


def test_replay_never_invokes_live_call(tmp_path):
    store = TemporalStore(tmp_path)
    gateway = TemporalGateway(store)
    request = {"ticker": "NVDA"}
    store.record("news", request, "captured", available_at=at(9))
    context = TemporalContext.at(TemporalMode.REPLAY, at(10), "scenario-1")

    def forbidden_live_call():
        raise AssertionError("replay must not use the network")

    outcome = gateway.invoke("news", request, context, forbidden_live_call)
    assert outcome.value == "captured"
    assert outcome.evidence is not None


def test_live_capture_records_a_result_that_can_be_replayed(tmp_path):
    store = TemporalStore(tmp_path)
    gateway = TemporalGateway(store)
    request = {"ticker": "NVDA"}
    captured_context = TemporalContext.at(TemporalMode.LIVE_CAPTURE, at(10))

    outcome = gateway.invoke("news", request, captured_context, lambda: {"headline": "captured"})

    assert outcome.evidence is not None
    replay_context = TemporalContext.at(
        TemporalMode.REPLAY,
        outcome.evidence.available_at + timedelta(microseconds=1),
    )
    replayed = gateway.invoke(
        "news", request, replay_context, lambda: pytest.fail("must not call live source")
    )
    assert replayed.value == {"headline": "captured"}
    assert [trace.mode for trace in store.list_tool_traces(captured_context.run_id)] == ["live_capture"]
    assert [trace.mode for trace in store.list_tool_traces(replay_context.run_id)] == ["replay"]


def test_replay_miss_is_explicit(tmp_path):
    gateway = TemporalGateway(TemporalStore(tmp_path))
    context = TemporalContext.at(TemporalMode.REPLAY, at(10))

    with pytest.raises(ReplayMissError, match="no eligible evidence"):
        gateway.invoke("news", {"ticker": "NVDA"}, context, lambda: "unexpected")


def test_live_capture_seals_failures_for_replay(tmp_path):
    store = TemporalStore(tmp_path)
    gateway = TemporalGateway(store)
    request = {"ticker": "NVDA"}
    capture = TemporalContext.at(TemporalMode.LIVE_CAPTURE, at(10))

    with pytest.raises(ValueError, match="rate limited"):
        gateway.invoke("news", request, capture, lambda: (_ for _ in ()).throw(ValueError("rate limited")))

    evidence = store.latest_eligible("news", request, as_of=datetime.now(UTC))
    assert evidence is not None
    assert evidence.is_error is True
    replay = TemporalContext.at(TemporalMode.REPLAY, evidence.available_at + timedelta(microseconds=1))
    with pytest.raises(ReplayedToolError, match="rate limited"):
        gateway.invoke("news", request, replay, lambda: pytest.fail("must not call live source"))


def test_temporal_context_is_scoped():
    context = TemporalContext.at(TemporalMode.REPLAY, at(10), "scenario-1")
    with temporal_context(context) as active:
        assert active.scenario_id == "scenario-1"
        assert current_context() is active
    assert current_context() is None


def test_scenario_snapshots_are_immutable(tmp_path):
    store = TemporalStore(tmp_path)
    first = store.seal_scenario_snapshot(
        "scenario-1",
        "agent.memory",
        {"context": "captured history"},
        captured_at=at(9),
    )

    assert store.get_scenario_snapshot("scenario-1", "agent.memory") == first
    assert (
        store.seal_scenario_snapshot(
            "scenario-1",
            "agent.memory",
            {"context": "captured history"},
            captured_at=at(10),
        )
        == first
    )
    with pytest.raises(ValueError, match="already sealed"):
        store.seal_scenario_snapshot(
            "scenario-1",
            "agent.memory",
            {"context": "changed"},
            captured_at=at(10),
        )


def test_scenario_definition_seals_time_basis_and_builds_context(tmp_path):
    store = TemporalStore(tmp_path)
    scenario = store.seal_scenario(
        "nvda-earnings",
        as_of=at(10),
        basis="archive-reconstructed",
        metadata={"ticker": "NVDA", "source_set": ["sec-edgar"]},
        capture_run_id="capture-run",
    )

    replay = TemporalContext.from_scenario(TemporalMode.REPLAY, store, "nvda-earnings")

    assert scenario.metadata["ticker"] == "NVDA"
    assert replay.clock.as_of == at(10)
    assert replay.scenario_id == "nvda-earnings"
    assert (
        TemporalContext.from_scenario(
            TemporalMode.REPLAY, store, "nvda-earnings", use_capture_tape=True
        ).source_run_id
        == "capture-run"
    )
    assert store.verify_scenario_corpus("nvda-earnings") is True
    store.record("corpus.document", {"url": "late-import"}, {"text": "NVDA"}, available_at=at(9))
    assert store.verify_scenario_corpus("nvda-earnings") is False
    with pytest.raises(ValueError, match="already sealed"):
        store.seal_scenario(
            "nvda-earnings",
            as_of=at(11),
            basis="archive-reconstructed",
            metadata={"ticker": "NVDA"},
        )
