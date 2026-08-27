"""Deterministic evidence briefs built from the owned temporal retriever."""

from __future__ import annotations

from datetime import datetime

from .models import canonical_json
from .retriever import search_payload


def build_evidence_brief(
    store,
    ticker: str,
    as_of: datetime,
    k: int = 5,
    *,
    run_id: str | None = None,
    scenario_id: str | None = None,
    mode: str | None = None,
) -> dict:
    """Return the same canonical search payload used by the temporal tool.

    When a run identity is given, the underlying search manifest is recorded
    as a search trace, so evidence surfaced by the injected brief counts
    toward coverage and grounding exactly like an agent-issued search.
    """
    if k < 1:
        raise ValueError("k must be positive")
    response = store.search(ticker, as_of=as_of, limit=k)
    if run_id is not None:
        store.record_search_trace(
            run_id=run_id,
            scenario_id=scenario_id,
            mode=mode or "replay",
            manifest=response.manifest,
            invoked_at=as_of,
        )
    return search_payload(response)


def evidence_brief_text(store, ticker: str, as_of: datetime, k: int = 5) -> str:
    """Render a brief for prompt injection without changing its data contract."""
    return canonical_json(build_evidence_brief(store, ticker, as_of, k))
