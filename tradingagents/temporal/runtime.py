"""Run-scoped temporal state, kept separate from agent frameworks."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .clock import VirtualClock
from .models import TemporalMode
from .store import TemporalStore


@dataclass(frozen=True)
class TemporalContext:
    mode: TemporalMode
    clock: VirtualClock
    scenario_id: str | None = None
    store: TemporalStore | None = None
    run_id: str | None = None
    source_run_id: str | None = None

    @classmethod
    def at(
        cls,
        mode: TemporalMode,
        as_of: datetime,
        scenario_id: str | None = None,
        store: TemporalStore | str | Path | None = None,
        run_id: str | None = None,
        source_run_id: str | None = None,
    ) -> TemporalContext:
        resolved_store = TemporalStore(store) if isinstance(store, (str, Path)) else store
        return cls(
            mode=mode,
            clock=VirtualClock(as_of),
            scenario_id=scenario_id,
            store=resolved_store,
            run_id=run_id or str(uuid4()),
            source_run_id=source_run_id,
        )

    @classmethod
    def from_scenario(
        cls,
        mode: TemporalMode,
        store: TemporalStore,
        scenario_id: str,
        *,
        run_id: str | None = None,
        source_run_id: str | None = None,
        use_capture_tape: bool = False,
    ) -> TemporalContext:
        """Create a context at the sealed time of a stored scenario."""
        scenario = store.get_scenario(scenario_id)
        if scenario is None:
            raise KeyError(f"unknown scenario: {scenario_id}")
        if use_capture_tape and scenario.capture_run_id is None:
            raise ValueError(f"scenario has no capture run: {scenario_id}")
        return cls.at(
            mode,
            scenario.as_of,
            scenario_id=scenario.scenario_id,
            store=store,
            run_id=run_id,
            source_run_id=scenario.capture_run_id if use_capture_tape else source_run_id,
        )


_CURRENT_CONTEXT: ContextVar[TemporalContext | None] = ContextVar("temporal_context", default=None)


def current_context() -> TemporalContext | None:
    return _CURRENT_CONTEXT.get()


@contextmanager
def temporal_context(context: TemporalContext) -> Iterator[TemporalContext]:
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)
