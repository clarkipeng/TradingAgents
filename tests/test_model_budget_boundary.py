"""Exercise LangChain invocation, so callback swallowing cannot bypass admission."""
import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tradingagents.model_budget import DailyModelBudget, ModelBudgetExceeded
from tradingagents.temporal import TemporalContext, TemporalMode, TemporalStore, temporal_context
from tradingagents.temporal_adapters.langchain import LangChainTapeRecorder


class BudgetedFixtureModel(BaseChatModel):
    calls: int = 0
    max_tokens: int | None = 8
    fail: bool = False
    include_usage: bool = True

    @property
    def _llm_type(self):
        return "budget-fixture"

    def _get_invocation_params(self, stop=None, **kwargs):
        return {**super()._get_invocation_params(stop=stop, **kwargs), "max_tokens": self.max_tokens}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fixture provider failure")
        usage = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3} if self.include_usage else None
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok", usage_metadata=usage))])


def make_context(tmp_path, max_tokens):
    store = TemporalStore(tmp_path)
    context = TemporalContext.at(TemporalMode.LIVE_CAPTURE, datetime.now(timezone.utc), store=store)
    context.model_budget = DailyModelBudget(tmp_path, "day", max_tokens=max_tokens, max_cost="100")
    return store, context


@pytest.mark.parametrize("asynchronous", [False, True])
def test_budget_denial_stops_provider_at_real_invocation(tmp_path, asynchronous):
    store, context = make_context(tmp_path, 1)
    model = BudgetedFixtureModel(callbacks=[LangChainTapeRecorder(store)], cache=False)
    try:
        with temporal_context(context), pytest.raises(ModelBudgetExceeded):
            if asynchronous:
                asyncio.run(model.ainvoke("hello"))
            else:
                model.invoke("hello")
        assert model.calls == 0
    finally:
        store.close()


def test_missing_output_bound_stops_provider(tmp_path):
    store, context = make_context(tmp_path, 10000)
    model = BudgetedFixtureModel(max_tokens=None, callbacks=[LangChainTapeRecorder(store)])
    try:
        with temporal_context(context), pytest.raises(ValueError, match="output token limit"):
            model.invoke("hello")
        assert model.calls == 0
    finally:
        store.close()


@pytest.mark.parametrize("outcome", ["success", "missing_usage", "failure"])
def test_returned_usage_settles_but_ambiguous_attempts_remain_charged(tmp_path, outcome):
    store, context = make_context(tmp_path, 10000)
    model = BudgetedFixtureModel(fail=outcome == "failure", include_usage=outcome != "missing_usage", callbacks=[LangChainTapeRecorder(store)], cache=False)
    try:
        with temporal_context(context):
            if outcome == "failure":
                with pytest.raises(RuntimeError, match="fixture provider failure"):
                    model.invoke("hello")
            else:
                model.invoke("hello")
        with closing(sqlite3.connect(context.model_budget.path)) as db:
            reserved, settled = db.execute("SELECT tokens, settled_tokens FROM model_reservations").fetchone()
        assert model.calls == 1
        assert reserved > 8
        assert settled == (3 if outcome == "success" else None)
    finally:
        store.close()


def test_retry_is_a_new_reservation_and_keeps_the_failed_attempt(tmp_path):
    store, context = make_context(tmp_path, 10000)
    model = BudgetedFixtureModel(fail=True, callbacks=[LangChainTapeRecorder(store)], cache=False)
    try:
        with temporal_context(context):
            for _ in range(2):
                with pytest.raises(RuntimeError, match="fixture provider failure"):
                    model.invoke("same request")
        with closing(sqlite3.connect(context.model_budget.path)) as db:
            count, settled = db.execute("SELECT COUNT(*), COUNT(settled_tokens) FROM model_reservations").fetchone()
        assert model.calls == count == 2
        assert settled == 0
    finally:
        store.close()
