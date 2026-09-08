"""Capture and replay the actual multi-ticker, four-analyst portfolio path."""
import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import tradingagents.agents.analysts.sentiment_analyst as sentiment
import tradingagents.dataflows.execution_prices as execution_prices
import tradingagents.dataflows.interface as interface
import tradingagents.graph.trading_graph as graph_module
import tradingagents.llm_clients.factory as factory
import tradingagents.portfolio_run as portfolio_run
import tradingagents.temporal.gateway as gateway
from tradingagents.portfolio_replay import replay_production_portfolio_day
from tradingagents.portfolio_run import production_day_inputs, run_portfolio_day
from tradingagents.temporal import TemporalStore


class FixtureModel(FakeListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _get_invocation_params(self, stop=None, **kwargs):
        return {**super()._get_invocation_params(stop=stop, **kwargs), "max_tokens": 1200}


@pytest.mark.parametrize("mutation", [None, "report", "quote", "missing_tape"])
@pytest.mark.parametrize(("policy", "capture_age"), [
    ("close-legacy-v1", 1), ("next-open-v1", 1), ("next-open-v1", -2),
])
def test_actual_portfolio_capture_replays_without_providers(tmp_path, monkeypatch, mutation, policy, capture_age):
    # Keep this synthetic forward capture ahead of its actual observations,
    # independent of when the offline fixture runs.
    day = (datetime.now(timezone.utc) + timedelta(days=capture_age)).date().isoformat()
    network_attempts = []

    def forbidden_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("external network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(graph_module.TradingAgentsGraph, "resolve_instrument_context", lambda self, ticker, *args: f"Captured {ticker} identity")
    monkeypatch.setattr(sentiment, "fetch_stocktwits_messages", lambda *args, **kwargs: "fixture social")
    monkeypatch.setattr(sentiment, "fetch_reddit_posts", lambda *args, **kwargs: "fixture social")
    for method, vendors in interface.VENDOR_METHODS.items():
        response = f"Date,Close\n{day},100\n" if method == "get_stock_data" else "fixture evidence"
        monkeypatch.setitem(interface.VENDOR_METHODS, method, {vendor: (lambda *args, value=response, **kwargs: value) for vendor in vendors})

    def client(provider, model, **kwargs):
        response = '{"weights":{"NVDA":0.1,"AAPL":0.1}}' if model == "fixture-cio" else "Fixture analyst report."
        llm = FixtureModel(responses=[response], callbacks=kwargs.get("callbacks"), cache=False)
        return SimpleNamespace(get_llm=lambda: llm)

    monkeypatch.setattr(factory, "create_llm_client", client)
    monkeypatch.setattr(graph_module, "create_llm_client", client)
    # Keep ordinary graph artifacts in the fixture directory.
    monkeypatch.setitem(graph_module.DEFAULT_CONFIG, "results_dir", str(tmp_path / "results"))
    monkeypatch.setitem(graph_module.DEFAULT_CONFIG, "memory_log_path", str(tmp_path / "memory.md"))
    with TemporalStore(tmp_path / "store") as store:
        inputs = production_day_inputs(store, deep_model="fixture-cio", sweep_model="fixture-analyst")
        cutoff = datetime.fromisoformat(f"{day}T21:30:00+00:00")
        if capture_age < 0 and policy == "next-open-v1":
            # Logical trading day and UTC capture date can differ. The
            # production graph and replay must still use the same prompt day.
            cutoff += timedelta(hours=3)
        captured = run_portfolio_day(
            store, ["NVDA", "AAPL"], day=day, **inputs,
            execution_policy=policy, capture_cutoff=cutoff,
            decision_ready_clock=lambda: cutoff + timedelta(hours=1),
        )
        assert captured.get("status") != "failed_unsealed", captured.get("reason")
        if policy == "next-open-v1":
            assert captured["status"] == "pending_execution"
            from tradingagents.portfolio_execution import next_open_timing

            timing = next_open_timing(cutoff + timedelta(hours=1))
            wall_clock = [timing.submitted_at + timedelta(minutes=1)]

            class ObservedClock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return wall_clock[0]

            class Ticker:
                def __init__(self, symbol):
                    self.symbol = symbol

                def history(self, **options):
                    assert options == {
                        "start": timing.execution_session,
                        "end": (timing.submitted_at.date() + timedelta(days=1)).isoformat(),
                        "auto_adjust": False, "back_adjust": False,
                        "actions": True, "repair": False,
                    }
                    # The response arrives after execution started. A clock
                    # sampled before fetching cannot authorize this quote.
                    wall_clock[0] += timedelta(seconds=2)
                    return pd.DataFrame({"Open": [110 if self.symbol == "AAPL" else 90],
                                         "Stock Splits": [0], "Dividends": [0]},
                                        index=pd.to_datetime([timing.execution_session]))

            monkeypatch.setattr(execution_prices.yf, "Ticker", Ticker)
            monkeypatch.setattr(gateway, "datetime", ObservedClock)
            captured = portfolio_run.execute_pending_portfolio_day(
                store, day, **portfolio_run.production_execution_inputs(store),
                execution_clock=lambda: wall_clock[0],
            )
            assert captured["status"] == "completed", captured
            assert captured["capture_cutoff"] != captured["filled_at"]
        scenario_id = f"portfolio-{day}"
        scenario = store.get_scenario(scenario_id)
        assert scenario is not None
        assert len(scenario.metadata["replay_manifest"]["workers"]) == 2
        for stream in scenario.metadata["replay_manifest"]["workers"]:
            assert stream["llm_call_count"] >= 4

        monkeypatch.setattr(factory, "create_llm_client", forbidden_network)
        monkeypatch.setattr(execution_prices.yf, "Ticker", forbidden_network)
        monkeypatch.setattr(graph_module, "create_llm_client", forbidden_network)
        monkeypatch.setattr(graph_module.TradingAgentsGraph, "resolve_instrument_context", forbidden_network)
        monkeypatch.setattr(sentiment, "fetch_stocktwits_messages", forbidden_network)
        monkeypatch.setattr(sentiment, "fetch_reddit_posts", forbidden_network)
        for method, vendors in interface.VENDOR_METHODS.items():
            monkeypatch.setitem(interface.VENDOR_METHODS, method, dict.fromkeys(vendors, forbidden_network))
        if mutation == "report":
            propagate = graph_module.TradingAgentsGraph.propagate

            def changed_report(*args, **kwargs):
                state, signal = propagate(*args, **kwargs)
                state["news_report"] = "A changed graph output must reach the CIO boundary."
                return state, signal

            monkeypatch.setattr(graph_module.TradingAgentsGraph, "propagate", changed_report)
        elif mutation == "quote":
            if policy == "next-open-v1":
                execution_inputs = portfolio_run.production_execution_inputs

                def changed_quote(store):
                    quote_fn = execution_inputs(store)["execution_quote_fn"]
                    return {"execution_quote_fn": lambda *args: replace(quote_fn(*args), price=Decimal("999"))}

                monkeypatch.setattr(portfolio_run, "production_execution_inputs", changed_quote)
            else:
                monkeypatch.setattr(portfolio_run, "_last_close", lambda *args, **kwargs: Decimal("999"))
        elif mutation == "missing_tape":
            worker_id = scenario.metadata["replay_manifest"]["workers"][0]["run_id"]
            call_id = store.list_llm_calls(None, temporal_run_id=worker_id)[0].call_id
            with store._connect() as connection:
                connection.execute("DELETE FROM llm_calls WHERE call_id = ?", (call_id,))
        if mutation:
            message = {"report": "research differs", "quote": "quote differs", "missing_tape": "complete LLM"}[mutation]
            with pytest.raises(ValueError, match=message):
                replay_production_portfolio_day(store, scenario_id)
            assert network_attempts == []
            return
        replayed = replay_production_portfolio_day(store, scenario_id)
        assert replayed["execution"]["matches_seal"] is True
        assert replayed["execution"]["fills"]
        assert network_attempts == []
