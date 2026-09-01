import importlib.util
import sys


def _module():
    spec = importlib.util.spec_from_file_location("verify_x_cycle", "scripts/verify_x_cycle.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_x_cycle_accepts_cloud_media_db_url(monkeypatch):
    module = _module()
    monkeypatch.setattr(sys, "argv", ["verify_x_cycle.py", "2026-08-30", "--media-db-url", "postgresql://db/evidence"])

    args = module._parse_args()

    assert args.day == "2026-08-30"
    assert args.media_db_url == "postgresql://db/evidence"
