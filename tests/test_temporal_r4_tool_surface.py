import json
from datetime import datetime, timezone

import pytest

from tradingagents.temporal import TemporalContext, TemporalMode, TemporalStore, temporal_context
from tradingagents.temporal.documents import stable_doc_key
from tradingagents.temporal_adapters.langchain import (
    create_contextual_temporal_fetch_tool,
    create_contextual_temporal_overview_tool,
)

UTC = timezone.utc


def test_search_filters_recency_browsing_and_pagination_pin(tmp_path):
    store = TemporalStore(tmp_path / "store")
    for index, hour in enumerate((9, 10, 11)):
        store.record("corpus.document", {"url": f"https://sec.gov/{index}"},
                     {"title": ("NVDA annual filing" if index == 0 else "NVDA quarterly filing" if index == 1 else "NVDA current report"), "text": "NVDA financials"},
                     available_at=datetime(2025, 1, 2, hour, tzinfo=UTC))
    as_of = datetime(2025, 1, 2, 12, tzinfo=UTC)
    first = store.search("", as_of=as_of, limit=1, date_from="2025-01-02", date_to="2025-01-02")
    assert first.results[0].document.title == "NVDA current report"
    second = store.search("", as_of=as_of, limit=1, page=2,
                          date_from="2025-01-02", date_to="2025-01-02",
                          corpus_hash_pin=first.manifest.corpus_hash)
    assert second.results[0].document.title == "NVDA quarterly filing"
    with pytest.raises(ValueError, match="corpus_hash"):
        store.search("", as_of=as_of, limit=1, page=2, corpus_hash_pin="wrong")


def test_fetch_enforces_available_at_and_records_trace(tmp_path):
    store = TemporalStore(tmp_path / "store")
    future = store.record("corpus.document", {"url": "https://sec.gov/future"},
                          {"title": "Future", "text": "NVDA financials"},
                          available_at=datetime(2025, 1, 3, tzinfo=UTC))
    context = TemporalContext.at(TemporalMode.REPLAY, datetime(2025, 1, 2, tzinfo=UTC), store=store)
    fetch = create_contextual_temporal_fetch_tool()
    # An ineligible (future) document is refused as a correctable tool error
    # the model can see - never as content, and never as a run-fatal raise.
    with temporal_context(context):
        refused = json.loads(
            fetch.invoke({"doc_key": stable_doc_key(future.evidence_id), "page": 1})
        )
    assert "ineligible" in refused["error"]
    assert "result" not in refused
    assert "NVDA financials" not in json.dumps(refused)
    assert store.list_tool_traces(context.run_id) == []
    store.record("corpus.document", {"url": "https://sec.gov/nvda"},
                 {"title": "10-Q", "text": "NVDA financials " * 500},
                 available_at=datetime(2025, 1, 1, tzinfo=UTC))
    doc = store.search("financials", as_of=datetime(2025, 1, 2, tzinfo=UTC)).results[0].doc_key
    with temporal_context(context):
        payload = json.loads(fetch.invoke({"doc_key": doc, "page": 1}))
    assert len(payload["result"]["body"]) <= 4000
    assert payload["result"]["has_more"] is True
    assert store.list_tool_traces(context.run_id)[0].tool == "temporal_fetch"


def test_overview_reports_eligible_source_counts(tmp_path):
    store = TemporalStore(tmp_path / "store")
    store.record("corpus.document", {"url": "https://sec.gov/a"}, {"text": "a"}, available_at=datetime(2025, 1, 1, tzinfo=UTC))
    store.record("corpus.document", {"url": "https://news.example/b"}, {"text": "b"}, available_at=datetime(2025, 1, 3, tzinfo=UTC))
    context = TemporalContext.at(TemporalMode.REPLAY, datetime(2025, 1, 2, tzinfo=UTC), store=store)
    with temporal_context(context):
        result = json.loads(create_contextual_temporal_overview_tool().invoke({}))
    assert result["document_count"] == 1
    assert result["date_span"] == {"from": "2025-01-01", "to": "2025-01-01"}
