"""Capture and replay the actual multi-ticker, four-analyst portfolio path."""
import json
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import tradingagents.agents.analysts.sentiment_analyst as sentiment
import tradingagents.dataflows.interface as interface
import tradingagents.graph.trading_graph as graph_module
import tradingagents.llm_clients.factory as factory
import tradingagents.portfolio_run as portfolio_run
from tradingagents.portfolio_replay import replay_production_portfolio_day
from tradingagents.portfolio_run import production_day_inputs, run_portfolio_day
from tradingagents.temporal import TemporalStore


class FixtureModel(FakeListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _get_invocation_params(self, stop=None, **kwargs):
        return {**super()._get_invocation_params(stop=stop, **kwargs), "max_tokens": 1200}


@pytest.mark.parametrize("mutation", [None, "report", "quote", "missing_tape"])
def test_actual_portfolio_capture_replays_without_providers(tmp_path, monkeypatch, mutation):
    # Keep this synthetic forward capture ahead of its actual observations,
    # independent of when the offline fixture runs.
    day = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
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
        captured = run_portfolio_day(store, ["NVDA", "AAPL"], day=day, **inputs)
        assert captured.get("status") != "failed_unsealed", captured.get("reason")
        scenario_id = f"portfolio-{day}"
        scenario = store.get_scenario(scenario_id)
        assert scenario is not None
        summary = json.loads(store.read_artifact(store.get_research_run(captured["run_id"]).report_artifact_hash))
        assert summary["brief_policy"]["compaction_enabled"] is False
        assert summary["brief_policy"]["comparison"]["evidence_pending"] is True
        assert len(scenario.metadata["replay_manifest"]["workers"]) == 2
        for stream in scenario.metadata["replay_manifest"]["workers"]:
            assert stream["llm_call_count"] >= 4

        monkeypatch.setattr(factory, "create_llm_client", forbidden_network)
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
