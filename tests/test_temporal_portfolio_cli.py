from pathlib import Path

from typer.testing import CliRunner

from cli.main import app


def test_portfolio_cli_exits_nonzero_for_unsealed_outcome(monkeypatch, tmp_path):
    import tradingagents.portfolio_run as portfolio

    monkeypatch.setattr(portfolio, "production_day_inputs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(portfolio, "run_portfolio_day", lambda *_args, **_kwargs: {
        "status": "failed_unsealed", "reason": "budget_exhausted"
    })
    result = CliRunner().invoke(app, ["temporal-portfolio-run", "--tickers", "NVDA", "--date", "2026-09-06", "--store", str(Path(tmp_path))])
    assert result.exit_code == 1
    assert "budget_exhausted" in result.output


def test_portfolio_cli_carries_actual_cutoff_into_pending_policy(monkeypatch, tmp_path):
    import tradingagents.portfolio_run as portfolio

    captured = {}
    monkeypatch.setattr(portfolio, "production_day_inputs", lambda *_args, **_kwargs: {})

    def run(*_args, **kwargs):
        captured.update(kwargs)
        return {"status": "pending_execution"}

    monkeypatch.setattr(portfolio, "run_portfolio_day", run)
    result = CliRunner().invoke(app, [
        "temporal-portfolio-run", "--tickers", "NVDA", "--date", "2026-09-06",
        "--capture-cutoff", "2026-09-06T22:17:03Z", "--store", str(Path(tmp_path)),
    ])
    assert result.exit_code == 0
    assert captured["capture_cutoff"].isoformat() == "2026-09-06T22:17:03+00:00"
    assert captured["execution_policy"] == "next-open-v1"


def test_portfolio_execute_cli_uses_production_pending_entrypoint(monkeypatch, tmp_path):
    import tradingagents.portfolio_run as portfolio

    monkeypatch.setattr(portfolio, "production_execution_inputs", lambda _store: {"execution_quote_fn": object()})
    monkeypatch.setattr(portfolio, "execute_pending_portfolio_day", lambda _store, day, **_kwargs: {
        "status": "completed", "day": day,
    })
    result = CliRunner().invoke(app, [
        "temporal-portfolio-execute", "--date", "2026-09-06", "--store", str(Path(tmp_path)),
    ])
    assert result.exit_code == 0
    assert '"status": "completed"' in result.output
