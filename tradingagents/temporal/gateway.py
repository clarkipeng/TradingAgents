"""The temporal boundary used by framework adapters and future MCP tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from .models import TemporalMode, TemporalOutcome, request_key
from .runtime import TemporalContext
from .store import TemporalStore


class ReplayMissError(LookupError):
    """Raised when replay has no captured evidence for an exact tool request."""


class ReplayedToolError(RuntimeError):
    """A captured source failure replayed without contacting the source again."""


class ReplayTapeMismatchError(RuntimeError):
    """A full replay diverged from its captured tool sequence."""


class TemporalGateway:
    """Capture/replay one tool call while enforcing a scenario's virtual clock."""

    def __init__(self, store: TemporalStore):
        self.store = store

    def invoke(
        self,
        tool: str,
        request: Mapping[str, Any],
        context: TemporalContext,
        live_call: Callable[[], Any],
        *,
        source: str | None = None,
    ) -> TemporalOutcome:
        if context.mode in (TemporalMode.REPLAY, TemporalMode.FROZEN_EVIDENCE):
            evidence = self._replay_evidence(tool, request, context)
            if evidence is None:
                self._record_trace(tool, request, context, None, outcome="missing-v1")
                raise ReplayMissError(
                    f"no eligible evidence for {tool!r} at {context.clock.as_of.isoformat()}"
                )
            if evidence.is_error:
                self._record_trace(tool, request, context, evidence.evidence_id)
                error = evidence.response
                raise ReplayedToolError(
                    f"replayed {error.get('error_type', 'tool error')}: "
                    f"{error.get('message', '')}"
                )
            self._record_trace(tool, request, context, evidence.evidence_id)
            return TemporalOutcome(value=evidence.response, evidence=evidence)

        # A virtual clock selects evidence.  It never changes when a live
        # response was observed: otherwise a backfill can label today's
        # mutable provider response as historical evidence.
        try:
            value = live_call()
        except Exception as error:
            observed_at = datetime.now(timezone.utc)
            if context.mode is TemporalMode.LIVE_CAPTURE:
                evidence = self.store.record_error(
                    tool,
                    request,
                    error,
                    available_at=observed_at,
                    observed_at=observed_at,
                    source=source,
                )
                self._record_trace(tool, request, context, evidence.evidence_id)
            raise
        if context.mode is TemporalMode.LIVE:
            return TemporalOutcome(value=value, evidence=None)

        observed_at = datetime.now(timezone.utc)
        captured_at = observed_at
        evidence = self.store.record(
            tool,
            request,
            value,
            available_at=captured_at,
            observed_at=captured_at,
            source=source,
        )
        self._record_trace(tool, request, context, evidence.evidence_id)
        return TemporalOutcome(value=value, evidence=evidence)

    def _replay_evidence(
        self,
        tool: str,
        request: Mapping[str, Any],
        context: TemporalContext,
    ):
        """Select time-eligible evidence, or the exact sequence from a sealed tool tape."""
        if context.source_run_id is None:
            return self.store.latest_eligible(tool, request, as_of=context.clock.as_of)
        if context.run_id is None:
            raise ReplayTapeMismatchError("full replay requires a replay run_id")
        sequence = len(self.store.list_tool_traces(context.run_id)) + 1
        captured = self.store.get_tool_trace(context.source_run_id, sequence)
        if captured is None:
            raise ReplayTapeMismatchError(
                f"tool tape exhausted at sequence {sequence} for run {context.source_run_id}"
            )
        if captured.tool != tool or captured.request_key != request_key(tool, request):
            raise ReplayTapeMismatchError(
                f"tool tape mismatch at sequence {sequence}: expected {captured.tool!r}"
            )
        if captured.outcome == "missing-v1" and captured.evidence_id is None:
            return None
        if captured.outcome != "evidence" or captured.evidence_id is None:
            raise ReplayTapeMismatchError(f"tool tape has no evidence at sequence {sequence}")
        evidence = self.store.get_evidence(captured.evidence_id)
        if evidence is None:
            raise ReplayTapeMismatchError(f"tool tape evidence is missing at sequence {sequence}")
        return evidence

    def _record_trace(
        self,
        tool: str,
        request: Mapping[str, Any],
        context: TemporalContext,
        evidence_id: str | None,
        *,
        outcome: str = "evidence",
    ) -> None:
        if context.run_id is None:
            return
        self.store.record_tool_trace(
            run_id=context.run_id,
            scenario_id=context.scenario_id,
            mode=context.mode.value,
            tool=tool,
            request=request,
            evidence_id=evidence_id,
            outcome=outcome,
        )
