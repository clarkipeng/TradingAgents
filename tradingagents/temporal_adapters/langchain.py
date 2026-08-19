"""Optional LangChain-only LLM tape recording for the temporal evidence store."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, message_to_dict, messages_from_dict
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, PrivateAttr

from tradingagents.temporal import LLMCallRecord, TemporalStore, canonical_json, current_context

logger = logging.getLogger(__name__)


def create_temporal_search_tool(store: TemporalStore) -> StructuredTool:
    """Create an optional LangChain tool over the owned time-filtered corpus.

    The active ``TemporalContext`` supplies the only time boundary. Results
    include their immutable evidence IDs and the corpus/ranker manifest, so a
    caller can cite evidence and detect corpus drift without a browser call.
    """

    return _build_temporal_search_tool(store)


def create_contextual_temporal_search_tool() -> StructuredTool:
    """Create a graph-safe temporal search tool that resolves its store per run.

    LangGraph builds its ``ToolNode`` before a run-scoped context exists. This
    variant lets a graph register the tool once, then reads the active run's
    store and clock only when the model actually calls it.
    """
    return _build_temporal_search_tool()


def _build_temporal_search_tool(expected_store: TemporalStore | None = None) -> StructuredTool:
    def search(query: str, limit: int = 10) -> str:
        context = current_context()
        if context is None or context.store is None:
            raise RuntimeError("temporal_search requires an active TemporalContext with a store")
        store = context.store
        if expected_store is not None and expected_store.root.resolve() != store.root.resolve():
            raise RuntimeError("temporal_search store does not match the active context")
        response = store.search(query, as_of=context.clock.as_of, limit=limit)
        if context.run_id is not None:
            store.record_search_trace(
                run_id=context.run_id,
                scenario_id=context.scenario_id,
                mode=context.mode.value,
                manifest=response.manifest,
                invoked_at=context.clock.as_of,
            )
        return canonical_json(
            {
                "results": [
                    {
                        "evidence_id": result.evidence.evidence_id,
                        "source": result.evidence.source,
                        "available_at": result.evidence.available_at,
                        "fidelity": result.evidence.fidelity,
                        "content": result.evidence.response,
                    }
                    for result in response.results
                ],
                "manifest": {
                    "query": response.manifest.query,
                    "as_of": response.manifest.as_of,
                    "ranker_version": response.manifest.ranker_version,
                    "corpus_hash": response.manifest.corpus_hash,
                    "evidence_ids": response.manifest.evidence_ids,
                },
            }
        )

    return StructuredTool.from_function(
        search,
        name="temporal_search",
        description=(
            "Search the owned public-evidence corpus at the active historical time. "
            "Cite returned evidence IDs in research as [evidence:<id>]."
        ),
    )


def _safe(value: Any) -> Any:
    """Convert callback metadata to JSON-safe data without leaking recorder failures upstream."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return repr(value)


def _serialize_result(response: LLMResult) -> dict[str, Any]:
    generations = []
    for batch in response.generations:
        generations.append(
            [
                {
                    "text": generation.text,
                    "message": message_to_dict(generation.message)
                    if getattr(generation, "message", None) is not None
                    else None,
                    "generation_info": _safe(generation.generation_info),
                }
                for generation in batch
            ]
        )
    return {"generations": generations, "llm_output": _safe(response.llm_output)}


def _stable_message_dict(message: BaseMessage | dict[str, Any]) -> dict[str, Any]:
    """Remove LangChain's per-invocation message UUID from a prompt fingerprint."""
    raw = message_to_dict(message) if isinstance(message, BaseMessage) else message
    payload = json.loads(canonical_json(raw))
    if isinstance(payload.get("data"), dict):
        payload["data"].pop("id", None)
    return payload


class LangChainTapeRecorder(BaseCallbackHandler):
    """Record model calls via LangChain callbacks without coupling the temporal core to LangChain."""

    raise_error = False

    def __init__(self, store: TemporalStore):
        self.store = store

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._begin(
            run_id,
            {
                "kind": "chat",
                "serialized": _safe(serialized),
                "messages": [[message_to_dict(message) for message in batch] for batch in messages],
                "invocation_params": _safe(kwargs.get("invocation_params")),
                "metadata": _safe(kwargs.get("metadata")),
            },
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._begin(
            run_id,
            {
                "kind": "text",
                "serialized": _safe(serialized),
                "prompts": prompts,
                "invocation_params": _safe(kwargs.get("invocation_params")),
                "metadata": _safe(kwargs.get("metadata")),
            },
        )

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **_kwargs: Any) -> None:
        self._finish(run_id, response=_serialize_result(response))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **_kwargs: Any) -> None:
        self._finish(
            run_id,
            error={"error_type": type(error).__name__, "message": str(error)},
        )

    def _begin(self, run_id: UUID, request: dict[str, Any]) -> None:
        context = current_context()
        try:
            self.store.begin_llm_call(
                str(run_id),
                request,
                started_at=datetime.now(timezone.utc),
                scenario_id=context.scenario_id if context else None,
                mode=context.mode.value if context else None,
                temporal_run_id=context.run_id if context else None,
            )
        except Exception:
            logger.exception("failed to persist temporal LLM request")

    def _finish(
        self,
        run_id: UUID,
        *,
        response: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
    ) -> None:
        try:
            self.store.finish_llm_call(
                str(run_id),
                response=response,
                error=error,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("failed to persist temporal LLM response")


class TapeMismatchError(RuntimeError):
    """The replay graph asked a different LLM question from the recorded tape."""


class ReplayedLLMError(RuntimeError):
    """A captured LLM-provider failure replayed without contacting a provider."""


class TapeChatModel(BaseChatModel):
    """A sequential, prompt-verifying LangChain chat model backed by recorded calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    calls: tuple[LLMCallRecord, ...]
    structured_output_replay: bool = False
    verify_prompts: bool = True
    _cursor: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "temporal-tape"

    @classmethod
    def from_store(
        cls,
        store: TemporalStore,
        scenario_id: str | None = None,
        *,
        source_run_id: str | None = None,
        structured_output_replay: bool = False,
        verify_prompts: bool = True,
    ) -> TapeChatModel:
        calls = tuple(
            call
            for call in store.list_llm_calls(scenario_id, temporal_run_id=source_run_id)
            if call.completed_at is not None
        )
        return cls(
            calls=calls,
            structured_output_replay=structured_output_replay,
            verify_prompts=verify_prompts,
        )

    @classmethod
    def from_scenario(
        cls,
        store: TemporalStore,
        scenario_id: str,
        *,
        structured_output_replay: bool = False,
        verify_prompts: bool = True,
    ) -> TapeChatModel:
        """Load the LLM tape named by a sealed full-trace scenario."""
        scenario = store.get_scenario(scenario_id)
        if scenario is None:
            raise KeyError(f"unknown scenario: {scenario_id}")
        if scenario.capture_run_id is None:
            raise ValueError(f"scenario has no capture run: {scenario_id}")
        return cls.from_store(
            store,
            scenario_id,
            source_run_id=scenario.capture_run_id,
            structured_output_replay=structured_output_replay,
            verify_prompts=verify_prompts,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **_kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        if self._cursor >= len(self.calls):
            raise TapeMismatchError("LLM tape exhausted")
        call = self.calls[self._cursor]
        self._cursor += 1
        if call.error is not None:
            raise ReplayedLLMError(
                f"replayed {call.error.get('error_type', 'LLM error')}: "
                f"{call.error.get('message', '')}"
            )
        if self.verify_prompts:
            self._verify_messages(call, messages)
        if call.response is None:
            raise TapeMismatchError(f"LLM tape call {call.call_id} has no response")
        generation = call.response["generations"][0][0]
        message_data = generation.get("message")
        if message_data is None:
            message = AIMessage(content=generation.get("text", ""))
        else:
            message = messages_from_dict([message_data])[0]
        if not isinstance(message, AIMessage):
            raise TapeMismatchError(f"LLM tape call {call.call_id} did not contain an AI response")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: str | None = None,
        **_kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        """Recorded responses already contain tool calls, so binding is replay metadata only."""
        del tools, tool_choice
        return self

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        **_kwargs: Any,
    ) -> Runnable[Any, Any]:
        """Parse a recorded tool-call payload into the schema expected by existing agents."""
        if not self.structured_output_replay:
            raise NotImplementedError("structured-output replay was not enabled for this tape")
        if include_raw:
            raise NotImplementedError("raw structured-output replay is not implemented")

        def parse(message: AIMessage) -> Any:
            if message.tool_calls:
                payload = message.tool_calls[0]["args"]
            else:
                try:
                    payload = json.loads(str(message.content))
                except json.JSONDecodeError as error:
                    raise ValueError("recorded structured output has no tool-call payload") from error
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                return schema.model_validate(payload)
            return payload

        return self | RunnableLambda(parse)

    @staticmethod
    def _verify_messages(call: LLMCallRecord, messages: list[BaseMessage]) -> None:
        request = call.request
        if request.get("kind") != "chat":
            raise TapeMismatchError(
                f"LLM tape call {call.call_id} is {request.get('kind', 'unknown')!r}, not chat"
            )
        expected_batches = request.get("messages") or []
        expected = [_stable_message_dict(message) for message in (expected_batches[0] if expected_batches else [])]
        actual = [_stable_message_dict(message) for message in messages]
        if canonical_json(expected) != canonical_json(actual):
            raise TapeMismatchError(f"LLM prompt mismatch at tape call {call.call_id}")
