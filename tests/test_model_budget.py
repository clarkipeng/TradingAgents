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
