"""Offline-only evidence for evaluating a proposed analyst-brief policy."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import tiktoken


def compare_frozen_briefs(baseline: str, candidate: str, *, scenario_id: str) -> dict[str, object]:
    """Describe two frozen inputs; this never enables or invokes a model."""
    if not scenario_id:
        raise ValueError("scenario_id is required")
    encoding = tiktoken.get_encoding("o200k_base")
    return {
        "schema_version": "brief-comparison-v1",
        "scenario_id": scenario_id,
        "baseline_sha256": sha256(baseline.encode()).hexdigest(),
        "candidate_sha256": sha256(candidate.encode()).hexdigest(),
        "baseline_tokens": len(encoding.encode(baseline)),
        "candidate_tokens": len(encoding.encode(candidate)),
        "quality_gate": "pending_fresh_model_evaluation",
    }


def compare_frozen_brief_files(baseline: Path, candidate: Path, *, scenario_id: str) -> dict[str, object]:
    """Load explicit local fixtures for the offline comparison command."""
    return compare_frozen_briefs(
        baseline.read_text(encoding="utf-8"), candidate.read_text(encoding="utf-8"), scenario_id=scenario_id,
    )
