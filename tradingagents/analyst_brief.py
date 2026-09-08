"""Versioned, deterministic analyst brief composition.

This module prepares an evaluation surface only.  The compaction policy is
disabled by default and cannot become model-visible until a frozen-scenario
comparison supplies evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

BRIEF_FORMAT_VERSION = "analyst-brief-v1"
COMPACTION_POLICY_VERSION = "analyst-compaction-disabled-v1"
ANALYST_ORDER = ("market", "sentiment", "news", "fundamentals")
_CITATION = re.compile(r"\[evidence:[^\]\s]+\]")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class AnalystBriefSection:
    analyst: str
    title: str
    text: str
    token_count: int
    citations: tuple[str, ...]
    dates: tuple[str, ...]


@dataclass(frozen=True)
class BriefComparison:
    format_version: str
    policy_version: str
    evidence_pending: bool
    baseline_tokens: int
    candidate_tokens: int
    baseline_citations: tuple[str, ...]
    candidate_citations: tuple[str, ...]
    baseline_dates: tuple[str, ...]
    candidate_dates: tuple[str, ...]

    @property
    def candidate_preserves_evidence(self) -> bool:
        return (
            set(self.baseline_citations) <= set(self.candidate_citations)
            and set(self.baseline_dates) <= set(self.candidate_dates)
        )


def _tokens(text: str) -> int:
    return len(re.findall(r"\S+", text))


def build_analyst_brief_sections(
    reports: Mapping[str, object], *, order: Sequence[str] = ANALYST_ORDER,
) -> tuple[AnalystBriefSection, ...]:
    """Build stable per-analyst sections without rewriting report content."""
    sections: list[AnalystBriefSection] = []
    for analyst in order:
        value = reports.get(analyst, "")
        text = value if isinstance(value, str) else ""
        sections.append(AnalystBriefSection(
            analyst=analyst,
            title=f"{analyst.title()} Analyst",
            text=text,
            token_count=_tokens(text),
            citations=tuple(dict.fromkeys(_CITATION.findall(text))),
            dates=tuple(dict.fromkeys(_DATE.findall(text))),
        ))
    return tuple(sections)


def render_analyst_brief(sections: Sequence[AnalystBriefSection]) -> str:
    """Render sections in caller-provided order, preserving each report byte."""
    return "\n\n".join(
        f"## {section.title}\n{section.text}" for section in sections if section.text
    )


def build_versioned_analyst_prompt(
    instructions: str, sections: Sequence[AnalystBriefSection], *,
    enable_compaction: bool = False,
) -> dict[str, object]:
    """Return prompt text plus accounting; experimental compaction stays off."""
    if enable_compaction:
        raise RuntimeError("analyst compaction requires frozen-scenario evidence")
    brief = render_analyst_brief(sections)
    prompt = f"{instructions}\n\n{brief}" if brief else instructions
    return {
        "format_version": BRIEF_FORMAT_VERSION,
        "policy_version": COMPACTION_POLICY_VERSION,
        "compaction_enabled": False,
        "prompt": prompt,
        "token_count": _tokens(prompt),
        "citations": tuple(dict.fromkeys(c for section in sections for c in section.citations)),
        "dates": tuple(dict.fromkeys(d for section in sections for d in section.dates)),
    }


def compare_brief_variants(
    baseline: str, candidate: str, *, scenario_id: str,
) -> BriefComparison:
    """Compare frozen text variants without invoking a model or provider."""
    if not scenario_id:
        raise ValueError("scenario_id is required")
    return BriefComparison(
        format_version=BRIEF_FORMAT_VERSION,
        policy_version=COMPACTION_POLICY_VERSION,
        evidence_pending=True,
        baseline_tokens=_tokens(baseline),
        candidate_tokens=_tokens(candidate),
        baseline_citations=tuple(dict.fromkeys(_CITATION.findall(baseline))),
        candidate_citations=tuple(dict.fromkeys(_CITATION.findall(candidate))),
        baseline_dates=tuple(dict.fromkeys(_DATE.findall(baseline))),
        candidate_dates=tuple(dict.fromkeys(_DATE.findall(candidate))),
    )
