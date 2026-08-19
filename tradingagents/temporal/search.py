"""Owned, deterministic temporal search over the evidence store."""

from __future__ import annotations

from datetime import datetime

from .models import TemporalSearchResponse
from .store import TemporalStore


class TemporalSearch:
    """A small agent-facing search facade; external provider replay is a separate tool tape."""

    def __init__(self, store: TemporalStore):
        self.store = store

    def search(self, query: str, *, as_of: datetime, limit: int = 10) -> TemporalSearchResponse:
        return self.store.search(query, as_of=as_of, limit=limit)
