"""Analysts must advertise the owned temporal search tool when it is enabled.

The tool being bound is not enough: analyst prompts prescribe a specific
tool workflow, so a model never reaches for an unadvertised auxiliary tool.
"""

from tradingagents.agents.utils.agent_utils import get_temporal_search_guidance
from tradingagents.dataflows.config import set_config


def test_guidance_is_empty_without_temporal_search():
    set_config({})
    assert get_temporal_search_guidance() == ""

    set_config({"temporal": {"mode": "replay", "search_enabled": False}})
    assert get_temporal_search_guidance() == ""


def test_guidance_names_the_tool_and_citation_format_when_enabled():
    set_config({"temporal": {"mode": "replay", "search_enabled": True}})
    try:
        guidance = get_temporal_search_guidance()
        assert "temporal_search" in guidance
        assert "[evidence:" in guidance
    finally:
        set_config({})


def test_brief_guidance_carries_the_brief_and_citation_format():
    from tradingagents.agents.utils.agent_utils import get_evidence_brief_guidance

    assert get_evidence_brief_guidance({}) == ""
    assert get_evidence_brief_guidance({"evidence_brief": ""}) == ""

    brief_text = '{"results":[{"evidence_id":"ev-1","title":"NVDA supply"}]}'
    guidance = get_evidence_brief_guidance({"evidence_brief": brief_text})
    assert brief_text in guidance
    assert "[evidence:" in guidance


def test_analyst_system_prompts_advertise_the_injected_brief():
    """The brief being in state is not enough: unless the prompt presents it,
    the model never reads it and the brief arm measures nothing."""
    import inspect

    from tradingagents.agents.analysts import (
        fundamentals_analyst,
        market_analyst,
        news_analyst,
    )

    for module in (market_analyst, news_analyst, fundamentals_analyst):
        assert "get_evidence_brief_guidance" in inspect.getsource(module)
