"""Durable per-day model-request reservations.

Reservations are made before provider work and remain charged when an outcome
is ambiguous.  This is intentionally a small sidecar ledger: it is shared by
all graph instances without widening the temporal evidence schema.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


class ModelBudgetExceeded(RuntimeError):
    """The day-wide request/token/money ceiling rejects another model call."""


@dataclass(frozen=True)
class BudgetReservation:
    call_id: str
    estimated_tokens: int
    estimated_cost: Decimal


class DailyModelBudget:
    def __init__(self, root: str | Path, day: str, *, max_tokens: int, max_cost: Decimal | str):
        self.path = Path(root) / "model-budget.sqlite"
        self.day = day
        self.max_tokens = max_tokens
        self.max_cost = Decimal(str(max_cost))
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS model_reservations (day TEXT NOT NULL, call_id TEXT NOT NULL, tokens INTEGER NOT NULL, cost TEXT NOT NULL, PRIMARY KEY(day, call_id))")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, isolation_level=None)
        try:
            yield connection
        finally:
            connection.close()

    def reserve(self, call_id: str, *, estimated_tokens: int, estimated_cost: Decimal | str) -> BudgetReservation:
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens must be non-negative")
        cost = Decimal(str(estimated_cost))
        if cost < 0:
            raise ValueError("estimated_cost must be non-negative")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT tokens, cost FROM model_reservations WHERE day = ? AND call_id = ?", (self.day, call_id)).fetchone()
            if prior:
                db.execute("COMMIT")
                return BudgetReservation(call_id, prior[0], Decimal(prior[1]))
            tokens, spent = db.execute("SELECT COALESCE(SUM(tokens), 0), COALESCE(SUM(CAST(cost AS REAL)), 0) FROM model_reservations WHERE day = ?", (self.day,)).fetchone()
            if tokens + estimated_tokens > self.max_tokens or Decimal(str(spent)) + cost > self.max_cost:
                db.execute("ROLLBACK")
                raise ModelBudgetExceeded("portfolio model budget exhausted")
            db.execute("INSERT INTO model_reservations(day, call_id, tokens, cost) VALUES (?, ?, ?, ?)", (self.day, call_id, estimated_tokens, str(cost)))
            db.execute("COMMIT")
        return BudgetReservation(call_id, estimated_tokens, cost)
