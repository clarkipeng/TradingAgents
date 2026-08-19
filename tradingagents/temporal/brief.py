"""Deterministic evidence briefs built from the owned temporal retriever."""

from __future__ import annotations

from datetime import datetime

from .models import canonical_json
from .retriever import search_payload


def build_evidence_brief(store, ticker: str, as_of: datetime, k: int = 5) -> dict:
    """Return the same canonical search payload used by the temporal tool."""
    if k < 1:
        raise ValueError("k must be positive")
    return search_payload(store.retriever.search(ticker, as_of=as_of, limit=k))


def evidence_brief_text(store, ticker: str, as_of: datetime, k: int = 5) -> str:
    """Render a brief for prompt injection without changing its data contract."""
    return canonical_json(build_evidence_brief(store, ticker, as_of, k))
