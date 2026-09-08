"""Offline-only evidence for evaluating a proposed analyst-brief policy."""

from __future__ import annotations

import os
import tempfile
from hashlib import sha1, sha256
from pathlib import Path

import tiktoken
from tiktoken.registry import ENCODINGS

_TOKENIZER = "o200k_base"
_TOKENIZER_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
_TOKENIZER_HASH = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"


def _offline_encoding():
    if _TOKENIZER not in ENCODINGS:
        cache = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get(
            "DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache"),
        ))
        path = Path(cache) / sha1(_TOKENIZER_URL.encode()).hexdigest()
        if not cache or not path.is_file() or sha256(path.read_bytes()).hexdigest() != _TOKENIZER_HASH:
            raise ValueError("offline_tokenizer_unavailable: prepare the o200k_base cache before comparison")
    return tiktoken.get_encoding(_TOKENIZER)


def compare_frozen_briefs(baseline: str, candidate: str, *, scenario_id: str) -> dict[str, object]:
    """Describe two frozen inputs; this never enables or invokes a model."""
    if not scenario_id:
        raise ValueError("scenario_id is required")
    encoding = _offline_encoding()
    return {
        "schema_version": "brief-comparison-v1",
        "scenario_id": scenario_id,
        "tokenizer": _TOKENIZER,
        "baseline_sha256": sha256(baseline.encode()).hexdigest(),
        "candidate_sha256": sha256(candidate.encode()).hexdigest(),
        "baseline_tokens": len(encoding.encode(baseline, disallowed_special=())),
        "candidate_tokens": len(encoding.encode(candidate, disallowed_special=())),
        "quality_gate": "pending_fresh_model_evaluation",
    }


def compare_frozen_brief_files(baseline: Path, candidate: Path, *, scenario_id: str) -> dict[str, object]:
    """Load explicit local fixtures for the offline comparison command."""
    return compare_frozen_briefs(
        baseline.read_text(encoding="utf-8"), candidate.read_text(encoding="utf-8"), scenario_id=scenario_id,
    )
