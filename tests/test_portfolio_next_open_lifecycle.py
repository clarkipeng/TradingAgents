from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier, Thread

import pandas as pd
import pytest

from tradingagents.portfolio_execution import (
    ExecutionQuote,
    ExecutionTiming,
    execute_next_open_targets,
)
from tradingagents.portfolio_replay import replay_production_portfolio_day
from tradingagents.portfolio_run import (
    execute_pending_portfolio_day,
    portfolio_research_result,
    production_execution_inputs,
    run_portfolio_day,
)
from tradingagents.temporal import TemporalStore

UTC = timezone.utc


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _publish_pending(store, *, day="2026-09-04", ready=None):
    ready = ready or _utc(2026, 9, 5, 0, 1)
    return run_portfolio_day(
        store, ["AAPL", "NVDA"], day=day,
        research_fn=lambda symbol, _context: portfolio_research_result(
            {"market_report": f"market-{symbol}", "sentiment_report": f"sentiment-{symbol}",
             "news_report": f"news-{symbol}", "fundamentals_report": f"fundamentals-{symbol}"},
            f"identity-{symbol}",
        ),
        complete_llm=lambda _prompt: '{"weights":{"AAPL":0.1,"NVDA":0.1}}',
        quote_fn=lambda *_args: pytest.fail("decision must not fetch execution quotes"),
        capture_cutoff=_utc(2026, 9, 4, 22),
        decision_ready_clock=lambda: ready,
        execution_policy="next-open-v1",
    )


def _quote(symbol, session, opened):
    price = Decimal("110") if symbol == "AAPL" else Decimal("90")
    return ExecutionQuote(symbol, session, price, opened, "raw-ohlcv-v1", f"evidence-{symbol}")


def test_production_pending_target_uses_actual_ready_time_and_holiday_open(tmp_path):
    with TemporalStore(tmp_path) as store:
        pending = _publish_pending(store)
        assert pending["status"] == "pending_execution"
        assert pending["capture_cutoff"] == "2026-09-04T22:00:00.000000Z"
        assert pending["decision_ready_at"] == "2026-09-05T00:01:00.000000Z"
        assert pending["execution_session"] == "2026-09-08"  # weekend + Labor Day
        assert store.portfolio_states() == []
        duplicate = _publish_pending(store)
        assert duplicate["skipped"] == "day already claimed"


def test_production_execution_applies_opening_gap_and_replays_its_quote_tape(tmp_path, monkeypatch):
    import tradingagents.dataflows.execution_prices as prices
    import tradingagents.temporal.gateway as gateway

    class ObservedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return _utc(2026, 9, 8, 14)

    monkeypatch.setattr(gateway, "datetime", ObservedClock)

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **options):
            assert options["auto_adjust"] is False and options["actions"] is True
            return pd.DataFrame({
                "Open": [110 if self.symbol == "AAPL" else 90],
                "Stock Splits": [0], "Dividends": [0],
            }, index=pd.to_datetime(["2026-09-08"]))

    monkeypatch.setattr(prices.yf, "Ticker", Ticker)
    with TemporalStore(tmp_path) as store:
        _publish_pending(store)
        result = execute_pending_portfolio_day(
            store, "2026-09-04", **production_execution_inputs(store),
            execution_clock=lambda: _utc(2026, 9, 8, 14),
        )
        assert result["status"] == "completed"
        assert {fill["price"] for fill in result["fills"]} == {"110.0220", "90.0180"}
        assert Decimal(result["final_state"]["cash"]) >= 0
        monkeypatch.setattr(prices.yf, "Ticker", lambda *_args: pytest.fail("replay contacted Yahoo"))
        class FixtureGraph:
            def propagate(self, symbol, *_args, **_kwargs):
                return ({"market_report": f"market-{symbol}", "sentiment_report": f"sentiment-{symbol}",
                         "news_report": f"news-{symbol}", "fundamentals_report": f"fundamentals-{symbol}"}, None)

        replay = replay_production_portfolio_day(
            store, "portfolio-2026-09-04",
            worker_graph_factory=lambda _tape: FixtureGraph(),
            cio_entrypoint=lambda _tape: result["plan"],
        )
        assert replay["execution"]["matches_seal"] is True


def test_held_position_actions_are_applied_before_rebalance():
    opened = _utc(2026, 9, 8, 13, 30)
    timing = ExecutionTiming(_utc(2026, 9, 4, 22), "2026-09-08", opened, opened)
    result = execute_next_open_targets(
        {"cash": "0", "positions": {"AAPL": "10"}},
        {"hold_book": True, "weights": {}},
        {"AAPL": ExecutionQuote(
            "AAPL", "2026-09-08", Decimal("50"), opened,
            "raw-ohlcv-v1", "action-evidence", split_ratio=Decimal("2"),
            cash_dividend=Decimal("1.5"),
        )}, timing=timing,
    )
    assert result["final_state"] == {"cash": "15.0", "positions": {"AAPL": "20"}, "equity": "1015.0"}


@pytest.mark.parametrize("mutation", ["missing", "late", "adjusted", "invalid"])
def test_execution_rejects_incomplete_late_or_invalid_raw_quotes(tmp_path, mutation):
    with TemporalStore(tmp_path) as store:
        _publish_pending(store)

        def broken(symbol, session, opened):
            if mutation == "missing" and symbol == "NVDA":
                raise KeyError(symbol)
            return ExecutionQuote(
                symbol, session, Decimal("-1") if mutation == "invalid" else Decimal("100"),
                _utc(2026, 9, 8, 14, 1) if mutation == "late" else opened,
                "adjusted" if mutation == "adjusted" else "raw-ohlcv-v1",
                f"evidence-{symbol}",
            )

        result = execute_pending_portfolio_day(
            store, "2026-09-04", execution_quote_fn=broken,
            execution_clock=lambda: _utc(2026, 9, 8, 14),
        )
        assert result["status"] == "failed_unsealed"
        assert store.portfolio_states() == []


def test_duplicate_execution_has_one_durable_winner(tmp_path):
    with TemporalStore(tmp_path) as store:
        _publish_pending(store)
        barrier = Barrier(2)
        outcomes = []

        def run():
            barrier.wait()
            outcomes.append(execute_pending_portfolio_day(
                store, "2026-09-04", execution_quote_fn=_quote,
                execution_clock=lambda: _utc(2026, 9, 8, 14),
            ))

        threads = [Thread(target=run), Thread(target=run)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(outcome.get("status") == "completed" for outcome in outcomes) == 1
        assert len(store.portfolio_states()) == 1


def test_pending_predecessor_blocks_new_research_and_is_retained(tmp_path):
    with TemporalStore(tmp_path) as store:
        _publish_pending(store)
        result = _publish_pending(store, day="2026-09-08", ready=_utc(2026, 9, 8, 22))
        assert result["reason"] == "pending_predecessor"
        decision = store.portfolio_decision("2026-09-04")
        retention = store.retention_plan(before=_utc(2027, 1, 1, 0))
        assert decision["payload_artifact_hash"] not in retention["artifacts"]["unreferenced_hashes"]
        assert decision["capture_run_id"] in retention["protected_run_ids"]
        assert f"{decision['capture_run_id']}:research:AAPL" in retention["protected_run_ids"]


def test_interrupted_execution_recovers_under_the_chain_lock(tmp_path):
    with TemporalStore(tmp_path) as store:
        _publish_pending(store)
        store.claim_portfolio_execution("2026-09-04", "dead-owner", claimed_at=_utc(2026, 9, 8, 13, 30))
        result = execute_pending_portfolio_day(store, "2026-09-04", execution_quote_fn=_quote,
                                             execution_clock=lambda: _utc(2026, 9, 8, 14))
        assert result["status"] == "completed"
        assert store.portfolio_execution("2026-09-04")["claim_id"] != "dead-owner"


def test_returns_do_not_silently_bridge_legacy_and_next_open_policies(tmp_path):
    from tradingagents.portfolio_run import portfolio_report

    with TemporalStore(tmp_path) as store:
        claim = store.claim_portfolio_day("2026-09-03", "legacy", claimed_at=_utc(2026, 9, 3, 22))
        store.complete_portfolio_day(
            "2026-09-03", claim["claim_id"], as_of=_utc(2026, 9, 3, 22),
            state={"cash": "100000", "positions": {}, "equity": "100000"},
            scenario_metadata={}, capture_run_id="legacy", decision="{}",
            report="{}",
        )
        _publish_pending(store)
        result = execute_pending_portfolio_day(
            store, "2026-09-04", execution_quote_fn=_quote,
            execution_clock=lambda: _utc(2026, 9, 8, 14),
        )
        assert result["status"] == "completed"
        report = portfolio_report(store)
        assert report["return_status"] == "mixed_execution_policies"
        assert report["total_return_pct"] is None
        assert report["days"][-1]["daily_return_pct"] is None
        assert report["days"][-1]["valuation_date"] == "2026-09-08"
        assert [row["execution_policy"] for row in report["days"]] == ["close-legacy-v1", "next-open-v1"]
