#!/usr/bin/env python3
"""Write a local, model-free comparison artifact for frozen analyst briefs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingagents.brief_evaluation import compare_frozen_brief_files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = compare_frozen_brief_files(args.baseline, args.candidate, scenario_id=args.scenario_id)
    args.output.write_text(json.dumps(artifact, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
