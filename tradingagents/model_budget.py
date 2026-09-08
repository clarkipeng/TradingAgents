"""Durable day-wide model reservations, valued at the configured token ceiling.

Unknown provider outcomes retain their reservation. Returned usage replaces
estimates; it is a policy valuation, not a provider invoice. SDK retries must
be disabled so every provider attempt passes this boundary.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from pathlib import Path

_MONEY_CONTEXT = Context(prec=50)


class ModelBudgetExceeded(RuntimeError):
    """The day-wide token or money ceiling rejects further model work."""


def _tokens(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token counts must be non-negative integers")
    return value


def _money(value: Decimal | str) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("costs must be finite and non-negative")
    return amount


@dataclass(frozen=True)
class BudgetReservation:
    call_id: str
    estimated_tokens: int
    estimated_cost: Decimal


class DailyModelBudget:
    def __init__(
        self, root: str | Path, day: str, *, max_tokens: int,
        max_cost: Decimal | str, cost_per_token: Decimal | str = "0.0001",
        cio_tokens: int = 0, cio_cost: Decimal | str = "0",
    ):
        self.path = Path(root) / "model-budget.sqlite"
        self.day = day
        self.max_tokens = _tokens(max_tokens)
        self.max_cost = _money(max_cost)
        self.cost_per_token = _money(cost_per_token)
        self.cio_tokens = _tokens(cio_tokens)
        self.cio_cost = _money(cio_cost)
        if self.cost_per_token == 0 or self.cio_tokens > self.max_tokens or self.cio_cost > self.max_cost:
            raise ValueError("invalid model budget policy")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS model_reservations (day TEXT NOT NULL, call_id TEXT NOT NULL, tokens INTEGER NOT NULL, cost TEXT NOT NULL, PRIMARY KEY(day, call_id))")
            columns = {row[1] for row in db.execute("PRAGMA table_info(model_reservations)")}
            for name, kind in (("settled_tokens", "INTEGER"), ("settled_cost", "TEXT"), ("role", "TEXT NOT NULL DEFAULT 'research'")):
                if name not in columns:
                    db.execute(f"ALTER TABLE model_reservations ADD COLUMN {name} {kind}")
            db.execute("COMMIT")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        try:
            yield connection
        finally:
            connection.close()

    def cost_for_tokens(self, tokens: int) -> Decimal:
        with localcontext(_MONEY_CONTEXT):
            return _tokens(tokens) * self.cost_per_token

    def _over_limit(self, db) -> bool:
        rows = db.execute(
            "SELECT COALESCE(settled_tokens, tokens), COALESCE(settled_cost, cost), role "
            "FROM model_reservations WHERE day = ?", (self.day,),
        ).fetchall()
        with localcontext(_MONEY_CONTEXT):
            research = [row for row in rows if row[2] != "cio"]
            return (
                sum(row[0] for row in rows) > self.max_tokens
                or sum((Decimal(row[1]) for row in rows), Decimal(0)) > self.max_cost
                or sum(row[0] for row in research) > self.max_tokens - self.cio_tokens
                or sum((Decimal(row[1]) for row in research), Decimal(0)) > self.max_cost - self.cio_cost
            )

    def reserve(self, call_id: str, *, estimated_tokens: int, estimated_cost: Decimal | str, role: str = "research") -> BudgetReservation:
        _tokens(estimated_tokens)
        cost = _money(estimated_cost)
        if not call_id or role not in {"research", "cio"}:
            raise ValueError("invalid model reservation identity")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT tokens, cost FROM model_reservations WHERE day = ? AND call_id = ?", (self.day, call_id)).fetchone()
            if prior:
                return BudgetReservation(call_id, prior[0], Decimal(prior[1]))
            db.execute("INSERT INTO model_reservations(day, call_id, tokens, cost, role) VALUES (?, ?, ?, ?, ?)", (self.day, call_id, estimated_tokens, str(cost), role))
            if self._over_limit(db):
                raise ModelBudgetExceeded("portfolio model budget exhausted")
            db.execute("COMMIT")
        return BudgetReservation(call_id, estimated_tokens, cost)

    def reconcile(self, call_id: str, *, actual_tokens: int) -> None:
        """Settle known usage once; retain overspend before rejecting more work."""
        _tokens(actual_tokens)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT settled_tokens FROM model_reservations WHERE day = ? AND call_id = ?", (self.day, call_id)).fetchone()
            if row is None:
                raise ValueError("model usage has no reservation")
            if row[0] is not None and row[0] != actual_tokens:
                raise ValueError("conflicting model usage settlement")
            db.execute("UPDATE model_reservations SET settled_tokens = ?, settled_cost = ? WHERE day = ? AND call_id = ?", (actual_tokens, str(self.cost_for_tokens(actual_tokens)), self.day, call_id))
            exceeded = self._over_limit(db)
            db.execute("COMMIT")
        if exceeded:
            raise ModelBudgetExceeded("returned model usage exceeded budget")
