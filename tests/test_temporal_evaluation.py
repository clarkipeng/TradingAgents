from datetime import datetime, timezone

import pytest

from tradingagents.temporal import (
    FactualClaim,
    RepeatedPairedScenarioResult,
    ResearchTrace,
    ScenarioRubric,
    TemporalStore,
    cited_claims_from_markdown,
    compare_pair,
    compare_recorded_runs,
    decision_stability,
    run_paired_evaluation,
    run_repeated_paired_evaluation,
    score_recorded_run,
    score_trace,
    trace_from_tool_run,
)
from tradingagents.temporal_adapters.tradingagents import replay_scenario


def trace(run_id, evidence_ids, claims=(), decision="HOLD"):
    return ResearchTrace(
        run_id=run_id,
        scenario_id="scenario-1",
        evidence_ids=tuple(evidence_ids),
        claims=tuple(claims),
        decision=decision,
    )


def test_trace_metrics_are_evidence_id_based():
    scored = score_trace(
        trace(
            "run-a",
            ["material-1", "noise"],
            [FactualClaim("claim-1", ("material-1",)), FactualClaim("claim-2", ())],
        ),
        material_evidence_ids=["material-1", "material-2"],
        useful_evidence_ids=["material-1"],
    )

    assert scored.evidence_coverage == 0.5
    assert scored.citation_grounding == 0.5
    assert scored.retrieval_efficiency == 0.5


def test_citation_grounding_requires_evidence_to_appear_in_the_run_trace():
    scored = score_trace(
        trace(
            "run-a",
            ["material-1"],
            [
                FactualClaim("grounded", ("material-1",)),
                FactualClaim("unretrieved", ("material-2",)),
            ],
        ),
        material_evidence_ids=["material-1", "material-2"],
        useful_evidence_ids=["material-1"],
    )

    assert scored.citation_grounding == 0.5


def test_cited_claims_from_markdown_uses_only_explicit_citation_lines():
    claims = cited_claims_from_markdown(
        "Uncited prose\nRevenue increased [evidence:abc] [evidence:def]\nRepeated [evidence:abc]"
    )

    assert claims == (
        FactualClaim("line:2", ("abc", "def")),
        FactualClaim("line:3", ("abc",)),
    )


def test_paired_comparison_and_decision_stability():
    left = trace("left", ["material-1"], [FactualClaim("claim", ("material-1",))], "BUY")
    right = trace(
        "right",
        ["material-1", "material-2"],
        [FactualClaim("claim", ("material-2",))],
        "HOLD",
    )

    comparison = compare_pair(
        left,
        right,
        material_evidence_ids=["material-1", "material-2"],
        useful_evidence_ids=["material-1", "material-2"],
    )

    assert comparison.evidence_coverage_delta == 0.5
    assert comparison.citation_grounding_delta == 0.0
    assert decision_stability(["BUY", "BUY", "HOLD"]) == 2 / 3


def test_paired_harness_uses_persisted_tool_traces_and_one_shared_rubric(tmp_path):
    store = TemporalStore(tmp_path)
    evidence = store.record(
        "news",
        {"ticker": "NVDA"},
        {"text": "earnings"},
        available_at="2025-01-02T09:00:00Z",
    )
    store.record_tool_trace(
        run_id="left-run",
        scenario_id="scenario-1",
        mode="replay",
        tool="news",
        request={"ticker": "NVDA"},
        evidence_id=evidence.evidence_id,
    )
    store.record_search_trace(
        run_id="left-run",
        scenario_id="scenario-1",
        mode="replay",
        manifest=store.search("earnings", as_of="2025-01-02T10:00:00Z").manifest,
    )
    left = trace_from_tool_run(
        store,
        run_id="left-run",
        scenario_id="scenario-1",
        claims=[FactualClaim("claim", (evidence.evidence_id,))],
        decision="BUY",
    )
    right = trace("right-run", [], (), "HOLD")
    results = run_paired_evaluation(
        [ScenarioRubric("scenario-1", (evidence.evidence_id,), (evidence.evidence_id,))],
        left_runner=lambda _scenario_id: left,
        right_runner=lambda _scenario_id: right,
    )

    assert left.evidence_ids == (evidence.evidence_id,)
    assert results[0].evidence_coverage_delta == -1.0


def test_repeated_paired_harness_reports_mean_quality_and_decision_stability():
    rubric = ScenarioRubric("scenario-1", ("material",), ("material",))
    left_decisions = ("BUY", "BUY", "HOLD")
    right_decisions = ("HOLD", "HOLD", "HOLD")

    def left_runner(scenario_id, repetition):
        return ResearchTrace(
            run_id=f"left-{repetition}",
            scenario_id=scenario_id,
            evidence_ids=("material",),
            claims=(FactualClaim("claim", ("material",)),),
            decision=left_decisions[repetition],
        )

    def right_runner(scenario_id, repetition):
        return ResearchTrace(
            run_id=f"right-{repetition}",
            scenario_id=scenario_id,
            evidence_ids=(),
            claims=(),
            decision=right_decisions[repetition],
        )

    results = run_repeated_paired_evaluation(
        [rubric],
        repetitions=3,
        left_runner=left_runner,
        right_runner=right_runner,
    )

    assert len(results) == 1
    assert isinstance(results[0], RepeatedPairedScenarioResult)
    assert results[0].left.evidence_coverage == 1.0
    assert results[0].right.evidence_coverage == 0.0
    assert results[0].left.decision_stability == 2 / 3
    assert results[0].right.decision_stability == 1.0
    assert results[0].decision_stability_delta == pytest.approx(1 / 3)


def test_repeated_paired_harness_rejects_zero_repetitions():
    with pytest.raises(ValueError, match="repetitions"):
        run_repeated_paired_evaluation(
            [],
            repetitions=0,
            left_runner=lambda *_args: pytest.fail("must not run"),
            right_runner=lambda *_args: pytest.fail("must not run"),
        )


def test_sealed_rubric_scores_a_persisted_trace(tmp_path):
    store = TemporalStore(tmp_path)
    evidence = store.record(
        "corpus.document",
        {"url": "https://example.com"},
        {"text": "NVDA earnings"},
        available_at=datetime(2025, 1, 2, 9, tzinfo=timezone.utc),
    )
    store.seal_scenario(
        "scenario-rubric",
        as_of=datetime(2025, 1, 2, 10, tzinfo=timezone.utc),
        basis="archive-reconstructed",
    )
    rubric = store.seal_scenario_rubric(
        "scenario-rubric",
        material_evidence_ids=(evidence.evidence_id,),
        useful_evidence_ids=(evidence.evidence_id,),
    )
    store.record_tool_trace(
        run_id="run-rubric",
        scenario_id="scenario-rubric",
        mode="replay",
        tool="temporal_search",
        request={"query": "NVDA"},
        evidence_id=evidence.evidence_id,
    )

    trace, metrics = score_recorded_run(
        store,
        run_id="run-rubric",
        scenario_id="scenario-rubric",
    )

    assert rubric.material_evidence_ids == (evidence.evidence_id,)
    assert trace.evidence_ids == (evidence.evidence_id,)
    assert metrics.evidence_coverage == metrics.retrieval_efficiency == 1.0


def test_sealed_rubric_compares_two_persisted_runs(tmp_path):
    store = TemporalStore(tmp_path)
    evidence = store.record(
        "corpus.document",
        {"url": "https://example.com/nvda"},
        {"text": "NVDA earnings"},
        available_at="2025-01-02T09:00:00Z",
    )
    store.seal_scenario(
        "scenario-comparison",
        as_of="2025-01-02T10:00:00Z",
        basis="archive-reconstructed",
    )
    store.seal_scenario_rubric(
        "scenario-comparison",
        material_evidence_ids=(evidence.evidence_id,),
        useful_evidence_ids=(evidence.evidence_id,),
    )
    store.record_tool_trace(
        run_id="baseline",
        scenario_id="scenario-comparison",
        mode="replay",
        tool="temporal_search",
        request={"query": "NVDA"},
        evidence_id=evidence.evidence_id,
    )

    left, right, comparison = compare_recorded_runs(
        store,
        left_run_id="baseline",
        right_run_id="changed",
        scenario_id="scenario-comparison",
    )

    assert left.evidence_ids == (evidence.evidence_id,)
    assert right.evidence_ids == ()
    assert comparison.evidence_coverage_delta == -1.0


def test_tradingagents_adapter_replays_a_sealed_scenario_into_a_trace(tmp_path):
    store = TemporalStore(tmp_path)
    evidence = store.record(
        "news",
        {"ticker": "NVDA"},
        {"text": "earnings"},
        available_at="2025-01-02T09:00:00Z",
    )
    store.seal_scenario(
        "nvda-q4",
        as_of="2025-01-02T10:00:00Z",
        basis="archive-reconstructed",
        metadata={"ticker": "NVDA", "trade_date": "2025-01-02"},
    )

    class Graph:
        def propagate(self, ticker, trade_date, *, asset_type, temporal):
            assert (ticker, trade_date, asset_type) == ("NVDA", "2025-01-02", "stock")
            store.record_tool_trace(
                run_id=temporal.run_id,
                scenario_id=temporal.scenario_id,
                mode=temporal.mode.value,
                tool="news",
                request={"ticker": "NVDA"},
                evidence_id=evidence.evidence_id,
            )
            return {"final_trade_decision": f"**Rating**: Buy [evidence:{evidence.evidence_id}]"}, "Buy"

    trace = replay_scenario(Graph(), store, "nvda-q4")

    assert trace.decision == f"**Rating**: Buy [evidence:{evidence.evidence_id}]"
    assert trace.evidence_ids == (evidence.evidence_id,)
    assert trace.claims == (FactualClaim("final-decision:1", (evidence.evidence_id,)),)


def test_tradingagents_replay_refuses_a_scenario_with_corpus_drift(tmp_path):
    store = TemporalStore(tmp_path)
    store.seal_scenario(
        "empty-world",
        as_of="2025-01-02T10:00:00Z",
        basis="archive-reconstructed",
        metadata={"ticker": "NVDA", "trade_date": "2025-01-02"},
    )
    store.record(
        "corpus.document",
        {"url": "backfilled"},
        {"text": "new evidence"},
        available_at="2025-01-02T09:00:00Z",
    )

    with pytest.raises(RuntimeError, match="corpus drift"):
        replay_scenario(object(), store, "empty-world")
