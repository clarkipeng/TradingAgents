"""End-to-end proof for a historical owned-search scenario in TradingAgents."""

import copy
from datetime import datetime, timezone

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.temporal import TemporalStore
from tradingagents.temporal_adapters.tradingagents import replay_scenario

UTC = timezone.utc


class _ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, _tools, **_kwargs):
        return self


def test_sealed_archive_scenario_replays_through_the_real_graph_owned_search(tmp_path):
    store = TemporalStore(tmp_path / "temporal")
    early = store.record(
        "corpus.document",
        {"url": "https://archive.example/nvda"},
        {"text": "NVDA archive filing reports data-center demand"},
        available_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
        fidelity="archive-reconstructed",
    )
    store.record(
        "corpus.document",
        {"url": "https://archive.example/future"},
        {"text": "NVDA future document must not be returned"},
        available_at=datetime(2025, 1, 2, 11, tzinfo=UTC),
        fidelity="archive-reconstructed",
    )
    store.seal_scenario(
        "nvda-archive",
        as_of=datetime(2025, 1, 2, 10, tzinfo=UTC),
        basis="archive-reconstructed",
        metadata={"ticker": "NVDA", "trade_date": "2025-01-02", "asset_type": "stock"},
    )
    model = _ToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "temporal_search",
                        "args": {"query": "NVDA archive filing"},
                        "id": "search-1",
                    }
                ],
            ),
            AIMessage(content="Archive news report [evidence:placeholder]"),
            AIMessage(content="HOLD"),
        ]
    )
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(
        {
            "data_cache_dir": str(tmp_path / "cache"),
            "results_dir": str(tmp_path / "results"),
            "memory_log_path": str(tmp_path / "memory.md"),
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
            "temporal": {
                "mode": "replay",
                "store": str(store.root),
                "scenario_id": "nvda-archive",
                "search_enabled": True,
            },
        }
    )
    graph = TradingAgentsGraph(
        selected_analysts=("news",),
        config=config,
        quick_thinking_llm=model,
        deep_thinking_llm=model,
    )
    graph.resolve_instrument_context = lambda *_args: ""

    trace = replay_scenario(graph, store, "nvda-archive")

    assert trace.scenario_id == "nvda-archive"
    assert trace.evidence_ids == (early.evidence_id,)
    assert len(store.list_search_traces(trace.run_id)) == 1
