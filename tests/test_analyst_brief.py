import pytest

from tradingagents.analyst_brief import (
    BRIEF_FORMAT_VERSION,
    COMPACTION_POLICY_VERSION,
    build_analyst_brief_sections,
    build_versioned_analyst_prompt,
    compare_brief_variants,
    render_analyst_brief,
)


def test_sections_preserve_dates_caveats_and_citations_deterministically():
    reports = {
        "news": "2026-09-07: catalyst [evidence:news-1]. Caveat: preliminary.",
        "market": "2026-09-06: trend [evidence:price-1].",
    }
    sections = build_analyst_brief_sections(reports)
    assert [section.analyst for section in sections] == ["market", "sentiment", "news", "fundamentals"]
    assert sections[0].text == reports["market"]
    assert sections[2].text == reports["news"]
    assert sections[2].dates == ("2026-09-07",)
    assert sections[2].citations == ("[evidence:news-1]",)
    rendered = render_analyst_brief(sections)
    assert "Caveat: preliminary." in rendered
    assert rendered.index("Market Analyst") < rendered.index("News Analyst")


def test_prompt_version_and_token_accounting_keep_compaction_disabled():
    sections = build_analyst_brief_sections({"market": "2026-09-07 [evidence:x]"})
    package = build_versioned_analyst_prompt("Use only supplied reports.", sections)
    assert package["format_version"] == BRIEF_FORMAT_VERSION
    assert package["policy_version"] == COMPACTION_POLICY_VERSION
    assert package["compaction_enabled"] is False
    assert package["token_count"] == len(package["prompt"].split())
    with pytest.raises(RuntimeError, match="frozen-scenario"):
        build_versioned_analyst_prompt("instructions", sections, enable_compaction=True)


def test_frozen_comparison_is_bounded_and_marks_quality_evidence_pending():
    comparison = compare_brief_variants(
        "2026-09-07 [evidence:x] Caveat: uncertain.",
        "2026-09-07 [evidence:x] Caveat: uncertain.",
        scenario_id="fixture-scenario",
    )
    assert comparison.evidence_pending is True
    assert comparison.candidate_preserves_evidence is True
    assert comparison.baseline_tokens == comparison.candidate_tokens
