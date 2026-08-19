"""Deterministic first-pass metrics for paired temporal research evaluations."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .store import TemporalStore


@dataclass(frozen=True)
class FactualClaim:
    claim_id: str
    cited_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResearchTrace:
    run_id: str
    scenario_id: str
    evidence_ids: tuple[str, ...]
    claims: tuple[FactualClaim, ...]
    decision: str | None


@dataclass(frozen=True)
class TraceMetrics:
    evidence_coverage: float | None
    citation_grounding: float | None
    retrieval_efficiency: float | None


@dataclass(frozen=True)
class PairedScenarioResult:
    scenario_id: str
    left: TraceMetrics
    right: TraceMetrics
    evidence_coverage_delta: float | None
    citation_grounding_delta: float | None
    retrieval_efficiency_delta: float | None


@dataclass(frozen=True)
class RepeatedArmMetrics:
    """Mean trace metrics and decision consistency over repeated agent runs."""

    repetitions: int
    evidence_coverage: float | None
    citation_grounding: float | None
    retrieval_efficiency: float | None
    decision_stability: float | None


@dataclass(frozen=True)
class RepeatedPairedScenarioResult:
    """A repeated A/B comparison for one fixed temporal scenario."""

    scenario_id: str
    left: RepeatedArmMetrics
    right: RepeatedArmMetrics
    evidence_coverage_delta: float | None
    citation_grounding_delta: float | None
    retrieval_efficiency_delta: float | None
    decision_stability_delta: float | None


@dataclass(frozen=True)
class ScenarioRubric:
    """The fixed relevance labels used to score both arms of one scenario."""

    scenario_id: str
    material_evidence_ids: tuple[str, ...]
    useful_evidence_ids: tuple[str, ...]


_EVIDENCE_CITATION = re.compile(r"\[evidence:([^\]\s]+)\]")


def cited_claims_from_markdown(text: str, *, claim_prefix: str = "line") -> tuple[FactualClaim, ...]:
    """Extract line-level claims from the lightweight ``[evidence:<id>]`` convention.

    This is a transparent default rather than an NLP claim extractor: only
    lines with explicit citations become scored claims, and callers can pass
    their own richer claim list to the evaluator.
    """
    claims = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        cited_ids = tuple(dict.fromkeys(_EVIDENCE_CITATION.findall(line)))
        if cited_ids:
            claims.append(FactualClaim(f"{claim_prefix}:{line_number}", cited_ids))
    return tuple(claims)


def score_trace(
    trace: ResearchTrace,
    *,
    material_evidence_ids: Iterable[str],
    useful_evidence_ids: Iterable[str],
) -> TraceMetrics:
    """Score a trace against an explicit scenario rubric.

    Material and useful evidence are deliberately inputs: this keeps the
    deterministic metric layer separate from later human/LLM relevance labels.
    """
    material = set(material_evidence_ids)
    useful = set(useful_evidence_ids)
    surfaced = set(trace.evidence_ids)
    coverage = len(surfaced & material) / len(material) if material else None
    efficiency = len(surfaced & useful) / len(surfaced) if surfaced else None
    claims = trace.claims
    # A citation is grounded only when the agent actually received that
    # eligible material evidence in this run. Referencing a relevant document
    # that was absent from the trace is not evidence for the claim.
    grounded = sum(bool(set(claim.cited_evidence_ids) & material & surfaced) for claim in claims)
    grounding = grounded / len(claims) if claims else None
    return TraceMetrics(
        evidence_coverage=coverage,
        citation_grounding=grounding,
        retrieval_efficiency=efficiency,
    )


def compare_pair(
    left: ResearchTrace,
    right: ResearchTrace,
    *,
    material_evidence_ids: Iterable[str],
    useful_evidence_ids: Iterable[str],
) -> PairedScenarioResult:
    """Compare two agent configurations on exactly one scenario."""
    if left.scenario_id != right.scenario_id:
        raise ValueError("paired traces must share a scenario")
    left_metrics = score_trace(
        left,
        material_evidence_ids=material_evidence_ids,
        useful_evidence_ids=useful_evidence_ids,
    )
    right_metrics = score_trace(
        right,
        material_evidence_ids=material_evidence_ids,
        useful_evidence_ids=useful_evidence_ids,
    )
    return PairedScenarioResult(
        scenario_id=left.scenario_id,
        left=left_metrics,
        right=right_metrics,
        evidence_coverage_delta=_delta(left_metrics.evidence_coverage, right_metrics.evidence_coverage),
        citation_grounding_delta=_delta(left_metrics.citation_grounding, right_metrics.citation_grounding),
        retrieval_efficiency_delta=_delta(
            left_metrics.retrieval_efficiency,
            right_metrics.retrieval_efficiency,
        ),
    )


def trace_from_tool_run(
    store: TemporalStore,
    *,
    run_id: str,
    scenario_id: str,
    claims: Iterable[FactualClaim] = (),
    decision: str | None = None,
) -> ResearchTrace:
    """Build a framework-neutral research trace from the persisted tool tape."""
    provided_claims = tuple(claims)
    stored_run = store.get_research_run(run_id)
    if stored_run is not None:
        if stored_run.scenario_id != scenario_id:
            raise ValueError("research run belongs to a different scenario")
        if decision is None:
            decision = stored_run.decision
    tool_traces = store.list_tool_traces(run_id)
    search_traces = store.list_search_traces(run_id)
    evidence_ids = [trace.evidence_id for trace in tool_traces if trace.evidence_id is not None]
    for trace in search_traces:
        evidence_ids.extend(trace.manifest.evidence_ids)
    return ResearchTrace(
        run_id=run_id,
        scenario_id=scenario_id,
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        claims=(
            provided_claims
            if provided_claims or not decision
            else cited_claims_from_markdown(decision, claim_prefix="final-decision")
        ),
        decision=decision,
    )


def score_recorded_run(
    store: TemporalStore,
    *,
    run_id: str,
    scenario_id: str,
    claims: Iterable[FactualClaim] = (),
    decision: str | None = None,
) -> tuple[ResearchTrace, TraceMetrics]:
    """Score one persisted tool/search trace against its sealed scenario rubric."""
    rubric = store.get_scenario_rubric(scenario_id)
    if rubric is None:
        raise KeyError(f"scenario has no sealed rubric: {scenario_id}")
    trace = trace_from_tool_run(
        store,
        run_id=run_id,
        scenario_id=scenario_id,
        claims=claims,
        decision=decision,
    )
    return trace, score_trace(
        trace,
        material_evidence_ids=rubric.material_evidence_ids,
        useful_evidence_ids=rubric.useful_evidence_ids,
    )


def compare_recorded_runs(
    store: TemporalStore,
    *,
    left_run_id: str,
    right_run_id: str,
    scenario_id: str,
    left_claims: Iterable[FactualClaim] = (),
    right_claims: Iterable[FactualClaim] = (),
    left_decision: str | None = None,
    right_decision: str | None = None,
) -> tuple[ResearchTrace, ResearchTrace, PairedScenarioResult]:
    """Compare two persisted replay runs against one immutable scenario rubric."""
    rubric = store.get_scenario_rubric(scenario_id)
    if rubric is None:
        raise KeyError(f"scenario has no sealed rubric: {scenario_id}")
    left, _left_metrics = score_recorded_run(
        store,
        run_id=left_run_id,
        scenario_id=scenario_id,
        claims=left_claims,
        decision=left_decision,
    )
    right, _right_metrics = score_recorded_run(
        store,
        run_id=right_run_id,
        scenario_id=scenario_id,
        claims=right_claims,
        decision=right_decision,
    )
    return left, right, compare_pair(
        left,
        right,
        material_evidence_ids=rubric.material_evidence_ids,
        useful_evidence_ids=rubric.useful_evidence_ids,
    )


def compare_recorded_run_sets(
    store: TemporalStore,
    *,
    left_run_ids: Iterable[str],
    right_run_ids: Iterable[str],
    scenario_id: str,
    decision_key: Callable[[str], str] | None = None,
) -> RepeatedPairedScenarioResult:
    """Summarize repeated persisted A/B runs, including decision stability."""
    left_ids = tuple(left_run_ids)
    right_ids = tuple(right_run_ids)
    if not left_ids or len(left_ids) != len(right_ids):
        raise ValueError("left_run_ids and right_run_ids must be non-empty and equally sized")
    rubric = store.get_scenario_rubric(scenario_id)
    if rubric is None:
        raise KeyError(f"scenario has no sealed rubric: {scenario_id}")
    results = run_repeated_paired_evaluation(
        [
            ScenarioRubric(
                scenario_id,
                rubric.material_evidence_ids,
                rubric.useful_evidence_ids,
            )
        ],
        repetitions=len(left_ids),
        left_runner=lambda _scenario_id, repetition: trace_from_tool_run(
            store,
            run_id=left_ids[repetition],
            scenario_id=scenario_id,
        ),
        right_runner=lambda _scenario_id, repetition: trace_from_tool_run(
            store,
            run_id=right_ids[repetition],
            scenario_id=scenario_id,
        ),
        decision_key=decision_key,
    )
    return results[0]


def run_paired_evaluation(
    rubrics: Iterable[ScenarioRubric],
    *,
    left_runner: Callable[[str], ResearchTrace],
    right_runner: Callable[[str], ResearchTrace],
) -> tuple[PairedScenarioResult, ...]:
    """Run two agent configurations against identical scenario IDs and rubrics.

    Runners own graph construction, models, and temporal contexts. The harness
    enforces only the invariant that both outputs use the same scenario rubric.
    """
    results = []
    for rubric in rubrics:
        left = left_runner(rubric.scenario_id)
        right = right_runner(rubric.scenario_id)
        if left.scenario_id != rubric.scenario_id or right.scenario_id != rubric.scenario_id:
            raise ValueError("runner returned a trace for a different scenario")
        results.append(
            compare_pair(
                left,
                right,
                material_evidence_ids=rubric.material_evidence_ids,
                useful_evidence_ids=rubric.useful_evidence_ids,
            )
        )
    return tuple(results)


def run_repeated_paired_evaluation(
    rubrics: Iterable[ScenarioRubric],
    *,
    repetitions: int,
    left_runner: Callable[[str, int], ResearchTrace],
    right_runner: Callable[[str, int], ResearchTrace],
    decision_key: Callable[[str], str] | None = None,
) -> tuple[RepeatedPairedScenarioResult, ...]:
    """Repeat paired evidence-replay experiments and summarize both arms.

    The caller owns how each repetition changes a model seed or temperature;
    the harness owns the invariant that each pair sees one scenario rubric.
    Repeating both arms is essential: stability is a property of a decision
    policy under the same historical world, not an after-the-fact label.
    """
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    results = []
    for rubric in rubrics:
        left_traces: list[ResearchTrace] = []
        right_traces: list[ResearchTrace] = []
        for repetition in range(repetitions):
            left = left_runner(rubric.scenario_id, repetition)
            right = right_runner(rubric.scenario_id, repetition)
            if left.scenario_id != rubric.scenario_id or right.scenario_id != rubric.scenario_id:
                raise ValueError("runner returned a trace for a different scenario")
            left_traces.append(left)
            right_traces.append(right)
        left_metrics = _summarize_repeated_traces(left_traces, rubric, decision_key)
        right_metrics = _summarize_repeated_traces(right_traces, rubric, decision_key)
        results.append(
            RepeatedPairedScenarioResult(
                scenario_id=rubric.scenario_id,
                left=left_metrics,
                right=right_metrics,
                evidence_coverage_delta=_delta(
                    left_metrics.evidence_coverage, right_metrics.evidence_coverage
                ),
                citation_grounding_delta=_delta(
                    left_metrics.citation_grounding, right_metrics.citation_grounding
                ),
                retrieval_efficiency_delta=_delta(
                    left_metrics.retrieval_efficiency, right_metrics.retrieval_efficiency
                ),
                decision_stability_delta=_delta(
                    left_metrics.decision_stability, right_metrics.decision_stability
                ),
            )
        )
    return tuple(results)


def decision_stability(
    decisions: Iterable[str | None],
    decision_key: Callable[[str], str] | None = None,
) -> float | None:
    """Return the fraction of repeated seeded runs that agree with the modal decision.

    ``decision_key`` normalizes each decision before comparison (for example,
    extracting the parsed rating from a markdown report) so prose variation
    between same-verdict runs does not read as instability.
    """
    non_empty = [decision for decision in decisions if decision is not None]
    if not non_empty:
        return None
    if decision_key is not None:
        non_empty = [decision_key(decision) for decision in non_empty]
    return max(non_empty.count(decision) for decision in set(non_empty)) / len(non_empty)


def _summarize_repeated_traces(
    traces: Iterable[ResearchTrace],
    rubric: ScenarioRubric,
    decision_key: Callable[[str], str] | None = None,
) -> RepeatedArmMetrics:
    trace_list = tuple(traces)
    metrics = tuple(
        score_trace(
            trace,
            material_evidence_ids=rubric.material_evidence_ids,
            useful_evidence_ids=rubric.useful_evidence_ids,
        )
        for trace in trace_list
    )
    return RepeatedArmMetrics(
        repetitions=len(trace_list),
        evidence_coverage=_mean(metric.evidence_coverage for metric in metrics),
        citation_grounding=_mean(metric.citation_grounding for metric in metrics),
        retrieval_efficiency=_mean(metric.retrieval_efficiency for metric in metrics),
        decision_stability=decision_stability(
            (trace.decision for trace in trace_list), decision_key
        ),
    )


def _delta(left: float | None, right: float | None) -> float | None:
    return right - left if left is not None and right is not None else None


def _mean(values: Iterable[float | None]) -> float | None:
    present = tuple(value for value in values if value is not None)
    return sum(present) / len(present) if present else None
