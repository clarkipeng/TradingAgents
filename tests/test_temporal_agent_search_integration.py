"""The optional owned search must work through an actual agent tool call."""

import json
from datetime import datetime, timezone

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.temporal import TemporalContext, TemporalMode, TemporalStore, temporal_context
from tradingagents.temporal_adapters.langchain import create_contextual_temporal_search_tool

UTC = timezone.utc


class _ToolCallingModel(FakeMessagesListChatModel):
    """The LangChain fake that can return tool-call messages lacks bind_tools."""

    def bind_tools(self, _tools, **_kwargs):
        return self


def test_news_agent_can_call_owned_archive_search_at_the_scenario_clock(tmp_path):
    store = TemporalStore(tmp_path)
    early = store.record(
        "corpus.document",
        {"url": "early"},
        {"text": "NVDA archive filing describes data-center demand"},
        available_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
        fidelity="archive-reconstructed",
    )
    store.record(
        "corpus.document",
        {"url": "future"},
        {"text": "NVDA archive filing from the future"},
        available_at=datetime(2025, 1, 2, 11, tzinfo=UTC),
        fidelity="archive-reconstructed",
    )
    context = TemporalContext.at(
        TemporalMode.REPLAY,
        datetime(2025, 1, 2, 10, tzinfo=UTC),
        scenario_id="nvda-history",
        store=store,
    )
    search_tool = create_contextual_temporal_search_tool()
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
            )
        ]
    )
    agent = create_news_analyst(model, extra_tools=(search_tool,))
    state = {
        "company_of_interest": "NVDA",
        "trade_date": "2025-01-02",
        "asset_type": "stock",
        "instrument_context": "",
        "messages": [],
    }

    with temporal_context(context):
        agent_result = agent(state)
        tool_call = agent_result["messages"][0].tool_calls[0]
        payload = json.loads(search_tool.invoke(tool_call["args"]))

    assert tool_call["name"] == "temporal_search"
    assert [item["evidence_id"] for item in payload["results"]] == [early.evidence_id]
    trace = store.list_search_traces(context.run_id)
    assert len(trace) == 1
    assert trace[0].manifest.evidence_ids == (early.evidence_id,)
