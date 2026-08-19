import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, message_to_dict
from langchain_core.outputs import ChatGeneration, LLMResult
from pydantic import BaseModel

from tradingagents.temporal import TemporalContext, TemporalMode, TemporalStore, temporal_context
from tradingagents.temporal_adapters.langchain import (
    LangChainTapeRecorder,
    TapeChatModel,
    TapeMismatchError,
    create_contextual_temporal_search_tool,
    create_temporal_search_tool,
)

UTC = timezone.utc


def test_langchain_recorder_persists_chat_request_and_response(tmp_path):
    store = TemporalStore(tmp_path)
    recorder = LangChainTapeRecorder(store)
    run_id = uuid4()
    context = TemporalContext.at(
        TemporalMode.LIVE_CAPTURE,
        datetime.now(UTC),
        scenario_id="scenario-1",
        store=store,
    )

    with temporal_context(context):
        recorder.on_chat_model_start(
            {"name": "test-model"},
            [[HumanMessage(content="hello")]],
            run_id=run_id,
            invocation_params={"model": "test-model", "temperature": 0},
        )
        recorder.on_llm_end(
            LLMResult(generations=[[ChatGeneration(message=AIMessage(content="world"))]]),
            run_id=run_id,
        )

    call = store.get_llm_call(str(run_id))
    assert call.scenario_id == "scenario-1"
    assert call.mode == "live_capture"
    assert call.temporal_run_id == context.run_id
    assert call.temporal_sequence == 1
    assert call.request["messages"][0][0]["data"]["content"] == "hello"
    assert call.response["generations"][0][0]["message"]["data"]["content"] == "world"
    assert call.error is None


def test_langchain_recorder_persists_errors(tmp_path):
    store = TemporalStore(tmp_path)
    recorder = LangChainTapeRecorder(store)
    run_id = uuid4()
    recorder.on_llm_start({"name": "test-model"}, ["hello"], run_id=run_id)
    recorder.on_llm_error(ValueError("provider unavailable"), run_id=run_id)

    call = store.get_llm_call(str(run_id))
    assert call.response is None
    assert call.error == {"error_type": "ValueError", "message": "provider unavailable"}


def _taped_chat_call(store, prompt, response, run_id, *, temporal_run_id=None, scenario_id=None):
    store.begin_llm_call(
        str(run_id),
        {
            "kind": "chat",
            "messages": [[message_to_dict(HumanMessage(content=prompt))]],
        },
        temporal_run_id=temporal_run_id,
        scenario_id=scenario_id,
    )
    store.finish_llm_call(
        str(run_id),
        response={
            "generations": [
                [
                    {
                        "text": response.content,
                        "message": message_to_dict(response),
                        "generation_info": None,
                    }
                ]
            ],
            "llm_output": None,
        },
    )


def test_tape_chat_model_replays_and_verifies_messages(tmp_path):
    store = TemporalStore(tmp_path)
    _taped_chat_call(store, "hello", AIMessage(content="world"), uuid4())
    model = TapeChatModel.from_store(store)

    assert model.invoke([HumanMessage(content="hello")]).content == "world"

    model = TapeChatModel.from_store(store)
    with pytest.raises(TapeMismatchError, match="prompt mismatch"):
        model.invoke([HumanMessage(content="different")])


def test_tape_chat_model_loads_the_capture_run_named_by_a_scenario(tmp_path):
    store = TemporalStore(tmp_path)
    store.seal_scenario(
        "golden",
        as_of=datetime(2025, 1, 2, 10, tzinfo=UTC),
        basis="forward-captured",
        capture_run_id="capture-1",
    )
    _taped_chat_call(
        store,
        "hello",
        AIMessage(content="world"),
        uuid4(),
        temporal_run_id="capture-1",
        scenario_id="golden",
    )

    model = TapeChatModel.from_scenario(store, "golden")

    assert model.invoke([HumanMessage(content="hello")]).content == "world"


def test_tape_chat_model_replays_structured_tool_output(tmp_path):
    class Answer(BaseModel):
        action: str

    store = TemporalStore(tmp_path)
    _taped_chat_call(
        store,
        "choose",
        AIMessage(content="", tool_calls=[{"name": "Answer", "args": {"action": "BUY"}, "id": "call-1"}]),
        uuid4(),
    )
    model = TapeChatModel.from_store(store, structured_output_replay=True)

    result = model.with_structured_output(Answer).invoke([HumanMessage(content="choose")])

    assert result == Answer(action="BUY")


def test_temporal_search_tool_uses_context_time_and_returns_citations(tmp_path):
    store = TemporalStore(tmp_path)
    store.record(
        "corpus.document",
        {"url": "early"},
        {"text": "NVDA supply constraints"},
        available_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
        source="https://example.com/early",
    )
    store.record(
        "corpus.document",
        {"url": "late"},
        {"text": "NVDA supply improves"},
        available_at=datetime(2025, 1, 2, 11, tzinfo=UTC),
    )
    tool = create_temporal_search_tool(store)
    context = TemporalContext.at(TemporalMode.REPLAY, datetime(2025, 1, 2, 10, tzinfo=UTC), store=store)

    with temporal_context(context):
        result = json.loads(tool.invoke({"query": "NVDA supply"}))

    assert [item["source"] for item in result["results"]] == ["https://example.com/early"]
    assert result["manifest"]["ranker_version"] == "sqlite-fts5-v1"
    assert result["manifest"]["corpus_hash"]
    assert store.list_search_traces(context.run_id)[0].manifest.evidence_ids == (
        result["results"][0]["evidence_id"],
    )


def test_contextual_temporal_search_resolves_the_run_store_at_call_time(tmp_path):
    store = TemporalStore(tmp_path)
    store.record(
        "corpus.document",
        {"url": "early"},
        {"text": "NVDA archive evidence"},
        available_at=datetime(2025, 1, 2, 9, tzinfo=UTC),
    )
    tool = create_contextual_temporal_search_tool()
    context = TemporalContext.at(TemporalMode.REPLAY, datetime(2025, 1, 2, 10, tzinfo=UTC), store=store)

    with temporal_context(context):
        result = json.loads(tool.invoke({"query": "NVDA archive"}))

    assert len(result["results"]) == 1
    assert store.list_search_traces(context.run_id)[0].manifest.evidence_ids == (
        result["results"][0]["evidence_id"],
    )
