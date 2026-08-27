#!/usr/bin/env python
"""Plan A driver: run the retrieval arms against sealed scenarios.

Three arms - fixed feeds (baseline), injected evidence brief, and
search-then-fetch - replay the same sealed scenario with everything but the
retrieval policy pinned. Every run tapes its LLM calls (token-level cost),
seals its decision in ``research_runs``, and appends its arm→run_id mapping
to a JSONL results file as it happens, so a crash never loses bookkeeping
(``research_runs`` itself has no arm column).

Usage:
    .venv/bin/python scripts/plan_a_experiment.py SCENARIO_ID [REPS] [ARM ...]

Defaults: 3 repetitions, all three arms, canonical store. Results append to
``.tradingagents/plan-a-runs.jsonl``; ready-to-run pairwise comparison
commands print at the end. Spends real LLM money (~$1-3 per run).
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv

load_dotenv(PROJECT_DIR / ".env", override=False)

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.temporal import TemporalStore
from tradingagents.temporal_adapters.langchain import LangChainTapeRecorder
from tradingagents.temporal_adapters.tradingagents import replay_scenario

STORE_PATH = os.environ.get(
    "TRADINGAGENTS_TEMPORAL_STORE", str(Path.home() / ".tradingagents" / "temporal")
)
RESULTS_PATH = PROJECT_DIR / ".tradingagents" / "plan-a-runs.jsonl"

ARMS = {
    "fixed_feeds": {},
    "brief": {"evidence_brief": True, "evidence_brief_k": 5},
    "search": {"search_enabled": True},
}

# Pinned identically across arms so retrieval policy is the only variable
# (model-weight contamination stays constant by construction).
PINNED = {
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.4",
    "quick_think_llm": "gpt-5.4-mini",
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "checkpoint_enabled": False,
}


def run_arm(store: TemporalStore, scenario_id: str, arm: str, rep: int) -> str:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(PINNED)
    config["temporal"] = {
        **DEFAULT_CONFIG["temporal"],
        "mode": "replay",
        "store": STORE_PATH,
        "scenario_id": scenario_id,
        **ARMS[arm],
    }
    graph = TradingAgentsGraph(
        config=config,
        callbacks=[LangChainTapeRecorder(store)],
    )
    started = time.monotonic()
    trace = replay_scenario(graph, store, scenario_id)
    record = {
        "scenario_id": scenario_id,
        "arm": arm,
        "rep": rep,
        "run_id": trace.run_id,
        "decision_head": (trace.decision or "")[:60],
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    with RESULTS_PATH.open("a") as handle:
        handle.write(json.dumps(record) + "\n")
    print(json.dumps(record), flush=True)
    return trace.run_id


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    scenario_id = sys.argv[1]
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    arms = sys.argv[3:] or list(ARMS)
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        print(f"unknown arm(s): {unknown}; choose from {list(ARMS)}", file=sys.stderr)
        return 2

    store = TemporalStore(STORE_PATH)
    store.verify_scenario_corpus(scenario_id)  # refuse drifted worlds up front
    if store.get_scenario_rubric(scenario_id) is None:
        print(f"warning: {scenario_id} has no sealed rubric - runs will not be "
              "coverage-scorable until one is sealed", file=sys.stderr)

    runs: dict[str, list[str]] = {arm: [] for arm in arms}
    for rep in range(reps):
        for arm in arms:  # interleave arms so drift-in-time affects all equally
            runs[arm].append(run_arm(store, scenario_id, arm, rep))

    print(json.dumps({"scenario_id": scenario_id, "runs": runs}))
    pairs = [(a, b) for index, a in enumerate(arms) for b in arms[index + 1:]]
    for left, right in pairs:
        print(
            f".venv/bin/tradingagents temporal-compare-repeated-runs "
            f"--left-run-ids {','.join(runs[left])} "
            f"--right-run-ids {','.join(runs[right])} "
            f"--id {scenario_id} --store {STORE_PATH}  # {left} vs {right}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
