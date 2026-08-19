from datetime import datetime, timezone

import pytest

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_collectors.wayback import import_wayback_captures

UTC = timezone.utc


class _Response:
    def __init__(self, *, payload=None, content=b"", text="", headers=None):
        self._payload = payload
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self):
        self.calls = []
        self.responses = [
            _Response(
                payload=[
                    ["timestamp", "original", "statuscode", "mimetype", "digest"],
                    ["20240221170203", "https://example.com/nvda", "200", "text/html", "digest-a"],
                ]
            ),
            _Response(
                content=b"<html><head><script>ignore()</script></head><body>NVDA <b>archive</b></body></html>",
                text="<html><head><script>ignore()</script></head><body>NVDA <b>archive</b></body></html>",
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
        ]

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_wayback_import_preserves_raw_html_and_uses_capture_time(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session()

    result = import_wayback_captures(
        store,
        url="https://example.com/nvda",
        start="2024-02-01",
        end="2024-02-29",
        session=session,
    )

    assert result.requested == result.imported == 1
    record = store.get_evidence(result.evidence_ids[0])
    assert record.available_at == datetime(2024, 2, 21, 17, 2, 3, tzinfo=UTC)
    assert record.fidelity == "archive-reconstructed"
    assert record.response["text"] == "NVDA archive"
    assert record.response["metadata"]["availability_basis"] == "wayback-capture"
    assert store.read_artifact(record.response["metadata"]["raw_artifact_hash"]).startswith(b"<html>")
    cdx_url, cdx_kwargs = session.calls[0]
    assert cdx_url.endswith("/cdx/search/cdx")
    assert cdx_kwargs["params"]["from"] == "20240201"
    assert cdx_kwargs["params"]["to"] == "20240229"
    assert "collapse" in cdx_kwargs["params"]
    assert "/web/20240221170203id_/https://example.com/nvda" in session.calls[1][0]


def test_wayback_import_rejects_invalid_boundaries_and_overeager_pacing(tmp_path):
    store = TemporalStore(tmp_path)

    with pytest.raises(ValueError, match="start must not be after end"):
        import_wayback_captures(store, url="https://example.com", start="2025", end="2024")
    with pytest.raises(ValueError, match="request_delay"):
        import_wayback_captures(store, url="https://example.com", request_delay_seconds=0)


def test_wayback_import_rechecks_cdx_capture_bounds(tmp_path):
    class Session:
        def get(self, _url, **_kwargs):
            return _Response(
                payload=[
                    ["timestamp", "original", "statuscode", "mimetype", "digest"],
                    ["20240220100000", "https://example.com/nvda", "200", "text/html", "old"],
                ]
            )

    result = import_wayback_captures(
        TemporalStore(tmp_path),
        url="https://example.com/nvda",
        start="20240221170203",
        end="20240228170203",
        session=Session(),
    )

    assert result.requested == result.imported == 0
