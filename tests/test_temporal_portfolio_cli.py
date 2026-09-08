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
