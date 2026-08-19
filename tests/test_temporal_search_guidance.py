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
