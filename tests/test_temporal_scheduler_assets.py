import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_assets_use_the_full_supported_capture_path():
    runner = (ROOT / "scripts" / "run_temporal_capture.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_temporal_launchd.sh").read_text(encoding="utf-8")
    universe = (ROOT / "config" / "temporal-universe.txt").read_text(encoding="utf-8")

    assert "temporal-capture" in runner
    assert "--full-surface" in runner
    assert "TRADINGAGENTS_TEMPORAL_STORE" in runner
    assert "!seen[$0]++" in runner
    assert "launchctl bootstrap" in installer
    assert "command -v" in installer
    assert "Temporal store path must be absolute" in installer
    symbols = [line for line in universe.splitlines() if line and not line.startswith("#")]
    assert 20 <= len(set(symbols)) <= 50
    assert len(symbols) == len(set(symbols))


def test_capture_runner_deduplicates_the_configured_universe(tmp_path):
    universe = tmp_path / "universe.txt"
    universe.write_text("AAPL\nNVDA\nAAPL\n", encoding="utf-8")
    recorded_args = tmp_path / "args.txt"
    command = tmp_path / "tradingagents"
    command.write_text('#!/bin/sh\nprintf "%s" "$*" > "$CAPTURE_ARGS"\n', encoding="utf-8")
    command.chmod(0o755)
    date = tmp_path / "date"
    date.write_text("#!/bin/sh\necho 2\n", encoding="utf-8")
    date.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TRADINGAGENTS_COMMAND": str(command),
        "TRADINGAGENTS_TEMPORAL_STORE": str(tmp_path / "corpus"),
        "TRADINGAGENTS_TEMPORAL_UNIVERSE_FILE": str(universe),
        "CAPTURE_ARGS": str(recorded_args),
    }

    subprocess.run(
        [ROOT / "scripts" / "run_temporal_capture.sh"],
        cwd=ROOT,
        env=environment,
        check=True,
    )

    assert recorded_args.read_text(encoding="utf-8") == (
        f"temporal-capture --tickers AAPL,NVDA --full-surface --store {tmp_path / 'corpus'}"
    )
