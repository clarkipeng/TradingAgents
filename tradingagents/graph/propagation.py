# TradingAgents/graph/propagation.py

from collections.abc import Mapping
from typing import Any

from tradingagents.agents.utils.agent_states import (
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.temporal import build_evidence_brief, canonical_json, current_context


class Propagator:
    """Handles state initialization and propagation through the graph."""

    def __init__(self, max_recur_limit=100, config: Mapping[str, Any] | None = None):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit
        self.config = config or {}

    def create_initial_state(
        self,
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        instrument_context: str = "",
    ) -> dict[str, Any]:
        """Create the initial state for the agent graph.

        ``instrument_context`` is the deterministic ticker-identity string
        resolved once at run start (see
        ``TradingAgentsGraph.resolve_instrument_context``). When empty, agents
        fall back to ticker-only context via
        ``get_instrument_context_from_state``.
        """
        state = {
            "messages": [("human", company_name)],
            "company_of_interest": company_name,
            "asset_type": asset_type,
            "instrument_context": instrument_context,
            "trade_date": str(trade_date),
            "past_context": past_context,
            "investment_debate_state": InvestDebateState(
                {
                    "bull_history": "",
                    "bear_history": "",
                    "history": "",
                    "current_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "risk_debate_state": RiskDebateState(
                {
                    "aggressive_history": "",
                    "conservative_history": "",
                    "neutral_history": "",
                    "history": "",
                    "latest_speaker": "",
                    "current_aggressive_response": "",
                    "current_conservative_response": "",
                    "current_neutral_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "market_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
        }
        state["evidence_brief"] = ""
        settings = self.config.get("temporal")
        context = current_context()
        if (
            isinstance(settings, Mapping)
            and settings.get("evidence_brief", False)
            and context is not None
            and context.store is not None
        ):
            # Rendered as text so analyst prompts can present it verbatim;
            # the run identity records the brief's manifest as a search trace
            # so its evidence counts toward coverage like any agent search.
            state["evidence_brief"] = canonical_json(build_evidence_brief(
                context.store, company_name, context.clock.as_of,
                int(settings.get("evidence_brief_k", 5)),
                run_id=context.run_id,
                scenario_id=context.scenario_id,
                mode=context.mode.value,
            ))
        return state

    def get_graph_args(self, callbacks: list | None = None) -> dict[str, Any]:
        """Get arguments for the graph invocation.

        Args:
            callbacks: Optional list of callback handlers for tool execution tracking.
                       Note: LLM callbacks are handled separately via LLM constructor.
        """
        config = {"recursion_limit": self.max_recur_limit}
        if callbacks:
            config["callbacks"] = callbacks
        return {
            "stream_mode": "values",
            "config": config,
        }
