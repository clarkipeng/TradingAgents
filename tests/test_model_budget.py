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
