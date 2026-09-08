from decimal import Decimal

import pytest

from tradingagents.model_budget import DailyModelBudget, ModelBudgetExceeded


def test_durable_budget_reserves_once_and_rejects_cross_instance_overspend(tmp_path):
    first = DailyModelBudget(tmp_path, "2026-09-06", max_tokens=10, max_cost="1")
    first.reserve("call-1", estimated_tokens=6, estimated_cost="0.50")
    # Retrying an ambiguous call preserves the original reservation.
    assert first.reserve("call-1", estimated_tokens=99, estimated_cost="99") .estimated_cost == Decimal("0.50")
    second = DailyModelBudget(tmp_path, "2026-09-06", max_tokens=10, max_cost="1")
    with pytest.raises(ModelBudgetExceeded):
        second.reserve("call-2", estimated_tokens=5, estimated_cost="0.10")


def test_budget_uses_decimal_cost_and_a_nonzero_default_token_estimate(tmp_path):
    budget = DailyModelBudget(
        tmp_path, "2026-09-06", max_tokens=100, max_cost="0.00019"
    )
    estimated_cost = budget.cost_per_token * 2
    assert estimated_cost == Decimal("0.0002")
    with pytest.raises(ModelBudgetExceeded):
        budget.reserve("call-1", estimated_tokens=2, estimated_cost=estimated_cost)


def test_budget_does_not_round_small_reservations_through_float(tmp_path):
    budget = DailyModelBudget(tmp_path, "2026-09-06", max_tokens=10, max_cost="0.3")
    budget.reserve("call-1", estimated_tokens=1, estimated_cost="0.1")
    budget.reserve("call-2", estimated_tokens=1, estimated_cost="0.2")
    with pytest.raises(ModelBudgetExceeded):
        budget.reserve("call-3", estimated_tokens=1, estimated_cost="0.0000001")


def test_portfolio_policy_declares_its_model_cost_estimate():
    from tradingagents.portfolio_run import PORTFOLIO_DAY_POLICY

    assert Decimal(PORTFOLIO_DAY_POLICY["model_cost_per_token"]) > 0


def test_usage_settlement_releases_only_known_usage_and_preserves_overrun(tmp_path):
    budget = DailyModelBudget(tmp_path, "day", max_tokens=10, max_cost="1")
    budget.reserve("one", estimated_tokens=8, estimated_cost="0.8")
    budget.reconcile("one", actual_tokens=2)
    budget.reconcile("one", actual_tokens=2)
    budget.reserve("two", estimated_tokens=8, estimated_cost="0.8")
    with pytest.raises(ValueError, match="conflicting"):
        budget.reconcile("one", actual_tokens=1)
    with pytest.raises(ModelBudgetExceeded):
        budget.reconcile("two", actual_tokens=20)
    restored = DailyModelBudget(tmp_path, "day", max_tokens=10, max_cost="1")
    with pytest.raises(ModelBudgetExceeded):
        restored.reserve("next", estimated_tokens=1, estimated_cost="0.01")


def test_cio_headroom_survives_research_exhaustion(tmp_path):
    budget = DailyModelBudget(tmp_path, "day", max_tokens=10, max_cost="1", cio_tokens=3, cio_cost="0.3")
    budget.reserve("research", estimated_tokens=7, estimated_cost="0.7")
    with pytest.raises(ModelBudgetExceeded):
        budget.reserve("research-next", estimated_tokens=1, estimated_cost="0.1")
    budget.reserve("cio", estimated_tokens=3, estimated_cost="0.3", role="cio")


def test_concurrent_reservations_cannot_overspend(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def reserve(index):
        budget = DailyModelBudget(tmp_path, "day", max_tokens=10, max_cost="1")
        try:
            budget.reserve(str(index), estimated_tokens=6, estimated_cost="0.6")
            return True
        except ModelBudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-0.1"])
def test_nonfinite_and_negative_costs_are_rejected(tmp_path, invalid):
    with pytest.raises(ValueError):
        DailyModelBudget(tmp_path, "day", max_tokens=10, max_cost=invalid)


def test_existing_ambiguous_reservations_survive_schema_upgrade(tmp_path):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(tmp_path / "model-budget.sqlite")) as db:
        db.execute("CREATE TABLE model_reservations (day TEXT NOT NULL, call_id TEXT NOT NULL, tokens INTEGER NOT NULL, cost TEXT NOT NULL, PRIMARY KEY(day, call_id))")
        db.execute("INSERT INTO model_reservations VALUES ('day', 'ambiguous', 9, '0.9')")
        db.commit()
    budget = DailyModelBudget(tmp_path, "day", max_tokens=10, max_cost="1")
    with pytest.raises(ModelBudgetExceeded):
        budget.reserve("retry", estimated_tokens=2, estimated_cost="0.2")


def test_decimal_context_cannot_change_cost_or_admission(tmp_path):
    from decimal import localcontext

    with localcontext() as ctx:
        ctx.prec = 2
        budget = DailyModelBudget(tmp_path, "day", max_tokens=10000, max_cost="0.30001", cost_per_token="0.00012345")
        assert budget.cost_for_tokens(1001) == Decimal("0.12357345")
        budget.reserve("one", estimated_tokens=1, estimated_cost="0.20001")
        with pytest.raises(ModelBudgetExceeded):
            budget.reserve("two", estimated_tokens=1, estimated_cost="0.10001")
