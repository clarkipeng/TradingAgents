from datetime import datetime, timezone

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_collectors.gdelt_wayback import import_gdelt_wayback_bodies

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


class _GdeltSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(
            payload={
                "articles": [
                    {
                        "url": "https://news.example/nvda",
                        "title": "NVIDIA data-center demand rises",
                        "seendate": "20240221T170203Z",
                    }
                ]
            }
        )


class _WaybackSession:
    def __init__(self):
        self.calls = []
        self.responses = [
            _Response(
                payload=[
                    ["timestamp", "original", "statuscode", "mimetype", "digest"],
                    ["20240222100000", "https://news.example/nvda", "200", "text/html", "digest-a"],
                ]
            ),
            _Response(
                content=b"<html><body>NVIDIA archived article body</body></html>",
                text="<html><body>NVIDIA archived article body</body></html>",
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
        ]

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_gdelt_wayback_bridge_preserves_discovery_lineage_and_capture_clock(tmp_path):
    store = TemporalStore(tmp_path)
    gdelt_session = _GdeltSession()
    wayback_session = _WaybackSession()

    result = import_gdelt_wayback_bodies(
        store,
        query="NVDA",
        start="2024-02-21",
        end="2024-02-21",
        max_capture_lag_days=7,
        gdelt_session=gdelt_session,
        wayback_session=wayback_session,
    )

    assert result.discovery.imported == result.attempted == result.imported == 1
    body = store.get_evidence(result.evidence_ids[0])
    assert body.available_at == datetime(2024, 2, 22, 10, tzinfo=UTC)
    assert body.response["text"] == "NVIDIA archived article body"
    lineage = body.response["metadata"]["lineage"]
    assert lineage["discovery_evidence_id"] == result.discovery.evidence_ids[0]
    assert lineage["discovery_query"] == "NVDA"
    cdx_params = wayback_session.calls[0][1]["params"]
    assert cdx_params["from"] == "20240221170203"
    assert cdx_params["to"] == "20240228170203"
