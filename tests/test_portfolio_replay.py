from datetime import datetime, timezone

import pytest

from tradingagents.portfolio_replay import (
    build_portfolio_replay_manifest,
    validate_portfolio_replay_manifest,
)
from tradingagents.temporal import TemporalStore


def test_portfolio_manifest_names_independent_worker_streams(tmp_path):
    store = TemporalStore(tmp_path)
    manifest = build_portfolio_replay_manifest(
        store, parent_run_id="day-1", tickers=["MSFT", "NVDA"]
    )

    assert [row["run_id"] for row in manifest["workers"]] == [
        "day-1:research:MSFT", "day-1:research:NVDA"
    ]
    validate_portfolio_replay_manifest(manifest)

    manifest["workers"][0]["run_id"] = "day-1"
    with pytest.raises(ValueError, match="ambiguous"):
        validate_portfolio_replay_manifest(manifest)
