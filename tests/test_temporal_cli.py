import json
from datetime import datetime, timezone

import requests
from typer.testing import CliRunner

import cli.main as cli
from tradingagents.temporal import TemporalStore
from tradingagents.temporal_adapters.tradingagents import DailyCaptureResult
from tradingagents.temporal_collectors import (
    GdeltImportResult,
    GdeltWaybackImportResult,
    HackerNewsImportResult,
    MediaStoreImportResult,
    RedditArchiveImportResult,
    SecEdgarImportResult,
    WaybackImportResult,
)


def test_temporal_import_command_builds_an_archive_reconstructed_corpus(tmp_path):
    archive = tmp_path / "archive.jsonl"
    archive.write_text(
        '{"source_url":"https://example.com/news","available_at":"2025-01-02T09:00:00Z",'
        '"document":{"text":"NVDA earnings"}}\n',
        encoding="utf-8",
    )
    store_path = tmp_path / "store"

    result = CliRunner().invoke(
        cli.app,
        ["temporal-import", str(archive), "--store", str(store_path)],
    )

    assert result.exit_code == 0, result.output
    assert "Imported 1 archive records" in result.output
    assert TemporalStore(store_path).search("NVDA", as_of="2025-01-02T10:00:00Z").results


def test_temporal_capture_command_is_scheduler_friendly(tmp_path, monkeypatch):
    captured = {}

    def fake_capture(store, tickers, *, news_lookback_days):
        captured.update({"store": store, "tickers": tuple(tickers), "days": news_lookback_days})
        return DailyCaptureResult(
            attempted=4,
            completed=4,
            failures=(),
            run_id="capture-1",
            captured_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            start_date="2024-12-26",
            end_date="2025-01-02",
        )

    monkeypatch.setattr(
        "tradingagents.temporal_adapters.tradingagents.capture_daily_market_research",
        fake_capture,
    )
    result = CliRunner().invoke(
        cli.app,
        ["temporal-capture", "--tickers", "nvda", "--store", str(tmp_path), "--news-lookback-days", "3", "--scenario-id", "nvda-forward"],
    )

    assert result.exit_code == 0, result.output
    assert captured["tickers"] == ("NVDA",)
    assert captured["days"] == 3
    assert "Captured 4/4" in result.output
    scenario = TemporalStore(tmp_path).get_scenario("nvda-forward")
    assert scenario is not None and scenario.capture_run_id == "capture-1"


def test_temporal_capture_can_request_the_extended_surface(tmp_path, monkeypatch):
    captured = {}

    def fake_capture(store, tickers, *, news_lookback_days, full_surface):
        captured.update({"tickers": tuple(tickers), "days": news_lookback_days, "full": full_surface})
        return DailyCaptureResult(
            attempted=15,
            completed=15,
            failures=(),
            run_id="capture-extended",
            captured_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            start_date="2024-12-26",
            end_date="2025-01-02",
        )

    monkeypatch.setattr(
        "tradingagents.temporal_adapters.tradingagents.capture_daily_market_research",
        fake_capture,
    )
    result = CliRunner().invoke(
        cli.app,
        ["temporal-capture", "--tickers", "nvda", "--store", str(tmp_path), "--full-surface"],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"tickers": ("NVDA",), "days": 7, "full": True}


def test_temporal_sec_import_command_passes_a_narrow_corpus_request(tmp_path, monkeypatch):
    captured = {}

    def fake_import(store, **kwargs):
        captured.update({"store": store, **kwargs})
        return SecEdgarImportResult(requested=2, imported=2, evidence_ids=("a", "b"), failures=())

    monkeypatch.setattr(
        "tradingagents.temporal_collectors.import_sec_edgar_filings",
        fake_import,
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-sec-import",
            "--cik",
            "1045810",
            "--user-agent",
            "TemporalResearch test@example.com",
            "--store",
            str(tmp_path),
            "--start-date",
            "2024-01-01",
            "--forms",
            "10-K, 8-K",
            "--max-filings",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["cik"] == "1045810"
    assert captured["forms"] == ("10-K", "8-K")
    assert captured["max_filings"] == 2
    assert "Imported 2/2 SEC filings" in result.output


def test_temporal_sec_import_reports_source_rejection_without_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tradingagents.temporal_collectors.import_sec_edgar_filings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.HTTPError("403 forbidden")),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-sec-import",
            "--cik",
            "1045810",
            "--user-agent",
            "TemporalResearch test@example.com",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "SEC import request failed" in result.output
    assert "Traceback" not in result.output


def test_temporal_wayback_import_passes_a_bounded_archive_request(tmp_path, monkeypatch):
    captured = {}

    def fake_import(store, **kwargs):
        captured.update({"store": store, **kwargs})
        return WaybackImportResult(requested=2, imported=2, evidence_ids=("a", "b"), failures=())

    monkeypatch.setattr(
        "tradingagents.temporal_collectors.import_wayback_captures",
        fake_import,
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-wayback-import",
            "--url",
            "https://example.com/nvda",
            "--from",
            "2024-01-01",
            "--to",
            "2024-03-01",
            "--max-captures",
            "2",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["url"] == "https://example.com/nvda"
    assert captured["start"] == "2024-01-01"
    assert captured["end"] == "2024-03-01"
    assert captured["max_captures"] == 2
    assert "Imported 2/2 Wayback captures" in result.output


def test_temporal_gdelt_import_passes_a_historical_discovery_request(tmp_path, monkeypatch):
    captured = {}

    def fake_import(store, **kwargs):
        captured.update({"store": store, **kwargs})
        return GdeltImportResult(
            requested=2,
            imported=2,
            evidence_ids=("a", "b"),
            failures=(),
            response_artifact_hash="raw-response",
        )

    monkeypatch.setattr(
        "tradingagents.temporal_collectors.import_gdelt_articles",
        fake_import,
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-gdelt-import",
            "--query",
            "NVDA",
            "--from",
            "2024-02-01",
            "--to",
            "2024-02-29",
            "--max-records",
            "2",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["query"] == "NVDA"
    assert captured["start"] == "2024-02-01"
    assert captured["end"] == "2024-02-29"
    assert captured["max_records"] == 2
    assert "Imported 2/2 GDELT article records" in result.output


def test_temporal_gdelt_wayback_import_passes_a_bounded_bridge_request(tmp_path, monkeypatch):
    captured = {}

    def fake_import(store, **kwargs):
        captured.update({"store": store, **kwargs})
        return GdeltWaybackImportResult(
            discovery=GdeltImportResult(
                requested=2,
                imported=2,
                evidence_ids=("discovery-a", "discovery-b"),
                failures=(),
                response_artifact_hash="raw-response",
            ),
            attempted=2,
            imported=1,
            evidence_ids=("body-a",),
            failures=(),
        )

    monkeypatch.setattr(
        "tradingagents.temporal_collectors.import_gdelt_wayback_bodies",
        fake_import,
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-gdelt-wayback-import",
            "--query",
            "NVDA",
            "--from",
            "2024-02-01",
            "--to",
            "2024-02-29",
            "--max-records",
            "2",
            "--max-capture-lag-days",
            "3",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["query"] == "NVDA"
    assert captured["max_records"] == 2
    assert captured["max_capture_lag_days"] == 3
    assert "Recovered 1/2 Wayback bodies" in result.output


def test_temporal_hn_import_passes_a_bounded_archive_request(tmp_path, monkeypatch):
    captured = {}

    def fake_import(store, **kwargs):
        captured.update({"store": store, **kwargs})
        return HackerNewsImportResult(
            requested=2,
            imported=2,
            evidence_ids=("a", "b"),
            failures=(),
            response_artifact_hash="raw-response",
        )

    monkeypatch.setattr(
        "tradingagents.temporal_collectors.import_hacker_news_stories",
        fake_import,
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-hn-import",
            "--query",
            "NVIDIA",
            "--from",
            "2024-02-01",
            "--to",
            "2024-02-29",
            "--max-records",
            "2",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["query"] == "NVIDIA"
    assert captured["start"] == "2024-02-01"
    assert captured["end"] == "2024-02-29"
    assert captured["max_records"] == 2
    assert "Imported 2/2 Hacker News stories" in result.output


def test_temporal_media_import_bridges_existing_poller_rows(tmp_path, monkeypatch):
    captured = {}

    def fake_import(store, **kwargs):
        captured.update({"store": store, **kwargs})
        return MediaStoreImportResult(requested=2, imported=2, evidence_ids=("a", "b"), failures=())

    monkeypatch.setattr(
        "tradingagents.temporal_collectors.import_media_store_posts",
        fake_import,
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-media-import",
            "--from",
            "2024-02-01",
            "--to",
            "2024-02-29",
            "--sources",
            "x,reddit",
            "--tickers",
            "nvda,msft",
            "--limit",
            "2",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["sources"] == ("x", "reddit")
    assert captured["tickers"] == ("NVDA", "MSFT")
    assert captured["limit"] == 2
    assert "Imported 2/2 poller media posts" in result.output


def test_temporal_reddit_import_passes_a_bounded_archive_request(tmp_path, monkeypatch):
    captured = {}

    def fake_import(store, **kwargs):
        captured.update({"store": store, **kwargs})
        return RedditArchiveImportResult(
            requested=2,
            imported=2,
            evidence_ids=("a", "b"),
            failures=(),
            response_artifact_hashes=("one",),
        )

    monkeypatch.setattr("tradingagents.temporal_collectors.import_reddit_archive", fake_import)
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-reddit-import",
            "--ticker",
            "nvda",
            "--from",
            "2024-02-01",
            "--to",
            "2024-02-29",
            "--subreddits",
            "stocks,investing",
            "--max-records",
            "2",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["ticker"] == "nvda"
    assert captured["subreddits"] == ("stocks", "investing")
    assert captured["max_records"] == 2
    assert "Imported 2/2 Reddit records" in result.output


def test_temporal_scenario_command_seals_a_manifest(tmp_path):
    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-scenario",
            "--id",
            "nvda-q4",
            "--as-of",
            "2024-02-21T17:02:03Z",
            "--basis",
            "archive-reconstructed",
            "--metadata",
            '{"ticker":"NVDA","sources":["sec-edgar"]}',
            "--capture-run-id",
            "run-123",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    scenario = TemporalStore(tmp_path).get_scenario("nvda-q4")
    assert scenario is not None
    assert scenario.metadata["sources"] == ["sec-edgar"]
    assert scenario.capture_run_id == "run-123"


def test_temporal_rubric_and_score_commands_use_sealed_evidence(tmp_path):
    store = TemporalStore(tmp_path)
    evidence = store.record(
        "corpus.document",
        {"url": "https://example.com"},
        {"text": "NVDA earnings"},
        available_at=datetime(2025, 1, 2, 9, tzinfo=timezone.utc),
    )
    store.seal_scenario(
        "nvda-rubric",
        as_of=datetime(2025, 1, 2, 10, tzinfo=timezone.utc),
        basis="archive-reconstructed",
    )
    store.record_tool_trace(
        run_id="run-rubric",
        scenario_id="nvda-rubric",
        mode="replay",
        tool="temporal_search",
        request={"query": "NVDA"},
        evidence_id=evidence.evidence_id,
    )

    rubric = CliRunner().invoke(
        cli.app,
        [
            "temporal-rubric",
            "--id",
            "nvda-rubric",
            "--material",
            evidence.evidence_id,
            "--useful",
            evidence.evidence_id,
            "--store",
            str(tmp_path),
        ],
    )
    scored = CliRunner().invoke(
        cli.app,
        ["temporal-score-run", "--run-id", "run-rubric", "--id", "nvda-rubric", "--store", str(tmp_path)],
    )

    assert rubric.exit_code == 0, rubric.output
    assert scored.exit_code == 0, scored.output
    assert json.loads(scored.output)["evidence_coverage"] == 1.0


def test_temporal_compare_runs_uses_the_same_sealed_rubric(tmp_path):
    store = TemporalStore(tmp_path)
    evidence = store.record(
        "corpus.document",
        {"url": "https://example.com"},
        {"text": "NVDA earnings"},
        available_at=datetime(2025, 1, 2, 9, tzinfo=timezone.utc),
    )
    store.seal_scenario(
        "nvda-comparison",
        as_of=datetime(2025, 1, 2, 10, tzinfo=timezone.utc),
        basis="archive-reconstructed",
    )
    store.seal_scenario_rubric(
        "nvda-comparison",
        material_evidence_ids=(evidence.evidence_id,),
        useful_evidence_ids=(evidence.evidence_id,),
    )
    store.record_tool_trace(
        run_id="baseline",
        scenario_id="nvda-comparison",
        mode="replay",
        tool="temporal_search",
        request={"query": "NVDA"},
        evidence_id=evidence.evidence_id,
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "temporal-compare-runs",
            "--left-run-id",
            "baseline",
            "--right-run-id",
            "changed",
            "--id",
            "nvda-comparison",
            "--store",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["evidence_coverage_delta"] == -1.0
