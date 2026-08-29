"""Optional LangChain-only LLM tape recording for the temporal evidence store."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
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
from tradingagents.temporal.retriever import search_payload

logger = logging.getLogger(__name__)


def create_temporal_search_tool(store: TemporalStore) -> StructuredTool:
    """Create an optional LangChain tool over the owned time-filtered corpus.

    The active ``TemporalContext`` supplies the only time boundary. Results
    include their immutable evidence IDs and the corpus/ranker manifest, so a
    caller can cite evidence and detect corpus drift without a browser call.
    """

    return _build_temporal_search_tool(store)


def create_temporal_fetch_tool(store: TemporalStore) -> StructuredTool:
    """Create the bounded normalized-document reader pinned to one store."""
    return _build_temporal_fetch_tool(store)


def create_contextual_temporal_search_tool() -> StructuredTool:
    """Create a graph-safe temporal search tool that resolves its store per run.

    LangGraph builds its ``ToolNode`` before a run-scoped context exists. This
    variant lets a graph register the tool once, then reads the active run's
    store and clock only when the model actually calls it.
    """
    return _build_temporal_search_tool()


def create_contextual_temporal_fetch_tool() -> StructuredTool:
    """Create a graph-safe bounded reader for normalized temporal documents."""
    return _build_temporal_fetch_tool()


def create_contextual_temporal_overview_tool() -> StructuredTool:
    """Create a graph-safe corpus metadata tool."""
    return _build_temporal_overview_tool()


def create_contextual_x_posts_tool() -> StructuredTool:
    """Create the X tool over the closed roster universe.

    The roster collector captures every subject every UTC day, so this tool
    answers identically in live and replay: it always reads the sealed corpus
    at the run's as_of, never the network. Subjects outside the roster are a
    correctable tool outcome that advertises the closed universe.
    """
    def x_posts(subject: str, days: int = 3) -> str:
        from tradingagents.dataflows.x_roster import X_ROSTER_TICKERS

        context, store = _context_store(None)
        normalized = (subject or "").strip().upper().lstrip("$")
        if normalized not in X_ROSTER_TICKERS:
            return canonical_json({
                "error": "subject is outside the captured X roster",
                "subjects": list(X_ROSTER_TICKERS),
            })
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 30:
            return canonical_json({
                "error": "days must be an integer between 1 and 30",
                "subject": normalized,
            })
        posts = store.media_posts_asof(
            normalized, as_of=context.clock.as_of, days=days
        )
        if context.run_id is not None:
            for post in posts:
                store.record_tool_trace(
                    run_id=context.run_id,
                    scenario_id=context.scenario_id,
                    mode=context.mode.value,
                    tool="x_posts",
                    request={"subject": normalized, "days": days},
                    evidence_id=post["evidence_id"],
                    invoked_at=context.clock.as_of,
                )
        return canonical_json({"subject": normalized, "days": days, "posts": posts})

    return StructuredTool.from_function(
        x_posts,
        name="x_posts",
        description=(
            "Captured X posts about one roster subject (major ticker symbol),"
            " newest first, restricted to what was available at the analysis"
            " time. Cite posts you rely on as [evidence:<id>]."
        ),
    )


# A matched document can be a full SEC filing (tens of MB); tool results feed
# straight into an LLM request, so each result carries a bounded snippet and a
# citation id instead of the whole document.
_SNIPPET_CHARS = 1_500


def _snippet(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping) and isinstance(response.get("text"), str):
        text = response["text"]
    else:
        text = canonical_json(response)
    if len(text) <= _SNIPPET_CHARS:
        return {"snippet": text, "truncated": False}
    return {"snippet": text[:_SNIPPET_CHARS], "truncated": True}


def _context_store(expected_store: TemporalStore | None) -> tuple[Any, TemporalStore]:
    context = current_context()
    if context is None or context.store is None:
        raise RuntimeError("temporal tool requires an active TemporalContext with a store")
    if expected_store is not None and expected_store.root.resolve() != context.store.root.resolve():
        raise RuntimeError("temporal tool store does not match the active context")
    return context, context.store


def _build_temporal_search_tool(expected_store: TemporalStore | None = None) -> StructuredTool:
    def search(query: str, limit: int = 10, page: int = 1, date_from: str | None = None,
               date_to: str | None = None, source: str | None = None,
               corpus_hash: str | None = None) -> str:
        context, store = _context_store(expected_store)
        try:
            response = store.search(query, as_of=context.clock.as_of, limit=limit, page=page,
                                    date_from=date_from, date_to=date_to, source=source,
                                    corpus_hash_pin=corpus_hash)
        except (KeyError, ValueError) as error:
            # Same degrade contract as temporal_fetch: bad model arguments
            # (malformed dates, stale corpus pins) come back as a payload.
            return canonical_json({"error": str(error).strip("'\""), "query": query})
        if context.run_id is not None:
            store.record_search_trace(
                run_id=context.run_id,
                scenario_id=context.scenario_id,
                mode=context.mode.value,
                manifest=response.manifest,
                invoked_at=context.clock.as_of,
            )
        return canonical_json(search_payload(response))

    return StructuredTool.from_function(
        search,
        name="temporal_search",
        description=(
            "Search the owned public-evidence corpus at the active historical time. "
            "Empty query lists eligible documents by recency; page 2+ requires the "
            "page-1 manifest corpus_hash. Cite returned evidence IDs as [evidence:<id>]."
        ),
    )


def _build_temporal_fetch_tool(expected_store: TemporalStore | None = None) -> StructuredTool:
    def fetch(doc_key: str, page: int = 1) -> str:
        context, store = _context_store(expected_store)
        try:
            result = store.fetch_document(doc_key, as_of=context.clock.as_of, page=page)
        except (KeyError, ValueError) as error:
            # Model-supplied arguments can be wrong (hallucinated doc keys,
            # out-of-range pages). That is a correctable tool outcome the
            # model must see, never a run-fatal exception (observed live
            # 2026-08-23: one bad key killed a whole replay run).
            return canonical_json({
                "error": str(error).strip("'\""),
                "doc_key": doc_key,
                "page": page,
            })
        if context.run_id is not None:
            store.record_tool_trace(run_id=context.run_id, scenario_id=context.scenario_id,
                                    mode=context.mode.value, tool="temporal_fetch",
                                    request={"doc_key": doc_key, "page": page},
                                    evidence_id=result["evidence_id"], invoked_at=context.clock.as_of)
        return canonical_json({"result": result})

    return StructuredTool.from_function(fetch, name="temporal_fetch",
        description="Read a bounded ~4k character page from an eligible normalized document. Fetch sequentially and cite its evidence_id.")


def _build_temporal_overview_tool(expected_store: TemporalStore | None = None) -> StructuredTool:
    def overview(source: str | None = None) -> str:
        context, store = _context_store(expected_store)
        return canonical_json(store.corpus_overview(as_of=context.clock.as_of, source=source))

    return StructuredTool.from_function(overview, name="corpus_overview",
        description="Summarize eligible temporal corpus source counts and available date span.")


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
