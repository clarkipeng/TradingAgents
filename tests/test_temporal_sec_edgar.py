from datetime import datetime, timezone

from tradingagents.temporal import TemporalStore
from tradingagents.temporal_collectors.sec_edgar import import_sec_edgar_filings

UTC = timezone.utc


class _Response:
    def __init__(self, *, payload=None, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self):
        self.calls = []
        self.responses = [
            _Response(
                payload={
                    "filings": {
                        "recent": {
                            "form": ["10-K", "8-K"],
                            "filingDate": ["2024-02-21", "2024-02-22"],
                            "reportDate": ["2024-01-28", ""],
                            "accessionNumber": ["0001045810-24-000029", "0001045810-24-000030"],
                            "primaryDocument": ["nvda10k.htm", "earnings.htm"],
                        }
                    }
                }
            ),
            _Response(text="<SEC-HEADER>\nACCEPTANCE-DATETIME: 20240221170203\n</SEC-HEADER>10-K"),
            _Response(text="<SEC-HEADER>8-K without an acceptance timestamp</SEC-HEADER>"),
        ]

    def get(self, url, *, headers, timeout):
        self.calls.append((url, headers, timeout))
        return self.responses.pop(0)


def test_sec_edgar_import_preserves_acceptance_or_uses_conservative_fallback(tmp_path):
    store = TemporalStore(tmp_path)
    session = _Session()
    delays = []

    result = import_sec_edgar_filings(
        store,
        cik="1045810",
        user_agent="TemporalResearch test@example.com",
        start_date="2024-02-01",
        end_date="2024-02-29",
        session=session,
        sleep=delays.append,
    )

    assert result.requested == 2
    assert result.imported == 2
    assert delays == [0.1]
    accepted = store.get_evidence(result.evidence_ids[0])
    fallback = store.get_evidence(result.evidence_ids[1])
    assert accepted.available_at == datetime(2024, 2, 21, 17, 2, 3, tzinfo=UTC)
    assert accepted.source_published_at == accepted.available_at
    assert accepted.event_at == datetime(2024, 1, 28, tzinfo=UTC)
    assert fallback.available_at == datetime(2024, 2, 22, 23, 59, 59, tzinfo=UTC)
    assert fallback.response["metadata"]["availability_basis"] == "filing-date-end"
    assert all("User-Agent" in headers for _, headers, _ in session.calls)
    assert session.calls[1][0].endswith("/000104581024000029/0001045810-24-000029.txt")


def test_sec_edgar_import_requires_a_contacting_user_agent(tmp_path):
    try:
        import_sec_edgar_filings(TemporalStore(tmp_path), cik="1045810", user_agent="")
    except ValueError as error:
        assert "user_agent" in str(error)
    else:
        raise AssertionError("collector should require an identifying user agent")
