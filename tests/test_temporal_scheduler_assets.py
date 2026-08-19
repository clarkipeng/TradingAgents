from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_assets_use_the_full_supported_capture_path():
    runner = (ROOT / "scripts" / "run_temporal_capture.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_temporal_launchd.sh").read_text(encoding="utf-8")
    universe = (ROOT / "config" / "temporal-universe.txt").read_text(encoding="utf-8")

    assert "temporal-capture" in runner
    assert "--full-surface" in runner
    assert "TRADINGAGENTS_TEMPORAL_STORE" in runner
    assert "launchctl bootstrap" in installer
    symbols = [line for line in universe.splitlines() if line and not line.startswith("#")]
    assert 20 <= len(set(symbols)) <= 50
