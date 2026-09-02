"""The cloud supervisor owns the trading machine's schedule.

Invariants under test: slots fire exactly once inside their grace window,
weekend slots for weekday jobs never fire, job commands are built from the
frozen universe with the slot's own date, and backups rotate in constant
space.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tradingagents.cloud_supervisor import (
    SCHEDULE_TZ,
    SLOT_GRACE,
    PollerChild,
    ScheduledJob,
    backup_store,
    build_jobs,
    due_jobs,
    universe_tickers,
)


@pytest.fixture()
def universe(tmp_path: Path) -> Path:
    path = tmp_path / "universe.txt"
    path.write_text("# frozen\nNVDA\nAAPL\n\nNVDA\nMSFT\n")
    return path


@pytest.fixture()
def env(universe: Path, tmp_path: Path) -> dict[str, str]:
    return {
        "TRADINGAGENTS_TEMPORAL_STORE": str(tmp_path / "temporal"),
        "TRADINGAGENTS_TEMPORAL_UNIVERSE_FILE": str(universe),
        "MEDIA_DB_URL": "postgresql+psycopg://example.invalid/media",
    }


def local(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SCHEDULE_TZ)


def test_universe_dedupes_and_strips_comments(universe: Path) -> None:
    assert universe_tickers(universe) == "NVDA,AAPL,MSFT"


def test_empty_universe_refuses(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    with pytest.raises(ValueError):
        universe_tickers(empty)


def test_slot_fires_once_within_grace(env: dict[str, str]) -> None:
    jobs = [job for job in build_jobs(env) if job.name == "portfolio-day"]
    fired: set[tuple[str, str]] = set()

    # 2026-09-02 is a Wednesday.
    before = local(2026, 9, 2, 17, 44)
    assert due_jobs(jobs, before, fired) == []

    at = local(2026, 9, 2, 17, 45)
    due = due_jobs(jobs, at, fired)
    assert [job.name for job, _ in due] == ["portfolio-day"]
    fired.add((due[0][0].name, due[0][1].isoformat()))

    later = at + SLOT_GRACE - timedelta(minutes=1)
    assert due_jobs(jobs, later, fired) == []

    past_grace = at + SLOT_GRACE + timedelta(minutes=1)
    assert due_jobs(jobs, past_grace, set()) == []


def test_weekday_jobs_skip_weekends(env: dict[str, str]) -> None:
    jobs = build_jobs(env)
    # 2026-09-05 is a Saturday.
    saturday_evening = local(2026, 9, 5, 19, 20)
    names = {job.name for job, _ in due_jobs(jobs, saturday_evening, set())}
    assert "cloud-media-import" in names
    assert "portfolio-day" not in names
    assert "temporal-capture" not in names


def test_portfolio_day_uses_the_slot_date(env: dict[str, str]) -> None:
    job = next(job for job in build_jobs(env) if job.name == "portfolio-day")
    slot = local(2026, 9, 2, 17, 45)
    argv = job.build(slot)
    assert argv[:2] == ["tradingagents", "temporal-portfolio-run"]
    assert argv[argv.index("--date") + 1] == "2026-09-02"
    assert argv[argv.index("--tickers") + 1] == "NVDA,AAPL,MSFT"
    assert argv[argv.index("--store") + 1] == env["TRADINGAGENTS_TEMPORAL_STORE"]


def test_media_import_spans_slot_yesterday_to_slot_day(env: dict[str, str]) -> None:
    job = next(job for job in build_jobs(env) if job.name == "cloud-media-import")
    argv = job.build(local(2026, 9, 2, 19, 15))
    assert argv[argv.index("--from") + 1] == "2026-09-01"
    assert argv[argv.index("--to") + 1] == "2026-09-02"
    assert argv[argv.index("--media-db-url") + 1] == env["MEDIA_DB_URL"]


def test_backup_rotates_by_weekday_and_overwrites(tmp_path: Path) -> None:
    store_dir = tmp_path / "temporal"
    store_dir.mkdir()
    db = store_dir / "temporal.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('first')")
    conn.commit()
    conn.close()

    backups = tmp_path / "backups"
    target = backup_store(store_dir, backups, weekday=2)
    assert target == backups / "temporal-2.sqlite3"
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT v FROM t").fetchall() == [("first",)]
    copy.close()

    conn = sqlite3.connect(db)
    conn.execute("UPDATE t SET v = 'second'")
    conn.commit()
    conn.close()
    backup_store(store_dir, backups, weekday=2)
    copy = sqlite3.connect(backups / "temporal-2.sqlite3")
    assert copy.execute("SELECT v FROM t").fetchall() == [("second",)]
    copy.close()
    assert sorted(p.name for p in backups.iterdir()) == ["temporal-2.sqlite3"]


def test_poller_child_pins_stores_to_the_local_volume(env: dict[str, str]) -> None:
    child = PollerChild(env)
    store = env["TRADINGAGENTS_TEMPORAL_STORE"]
    assert child.child_env["TRADINGAGENTS_POLLER_TEMPORAL_STORE"] == store
    # Never the machine-wide Postgres secret: that store belongs to the
    # collector's singleton lease.
    assert child.child_env["MEDIA_DB_URL"] == str(
        Path(store).parent / "media-poller.sqlite3"
    )


def test_poller_child_never_inherits_the_collectors_x_identity(
    env: dict[str, str],
) -> None:
    machine_env = {
        **env,
        "MEDIA_POLLER_SOURCES": "x",
        "MEDIA_POLLER_X_TOPICS": "5",
        "MEDIA_POLLER_TRADING_HOURS": "false",
    }
    child = PollerChild(machine_env)
    assert child.child_env["MEDIA_POLLER_SOURCES"] == "stocktwits,reddit,news"
    assert child.child_env["MEDIA_POLLER_TRADING_HOURS"] == "true"
    assert "MEDIA_POLLER_X_TOPICS" not in child.child_env


def test_poller_backoff_doubles_to_cap_and_resets_when_healthy(
    env: dict[str, str],
) -> None:
    child = PollerChild(env)
    waits = [child.next_backoff(healthy_seconds=1.0) for _ in range(7)]
    assert waits == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0]
    assert child.next_backoff(healthy_seconds=3600.0) == 30.0


def test_unseeded_volume_is_not_a_store(env: dict[str, str]) -> None:
    from tradingagents.cloud_supervisor import store_is_seeded

    assert not store_is_seeded(env)
    store = Path(env["TRADINGAGENTS_TEMPORAL_STORE"])
    store.mkdir(parents=True)
    (store / "temporal.sqlite3").touch()
    assert store_is_seeded(env)


def test_every_job_has_a_positive_timeout(env: dict[str, str]) -> None:
    for job in build_jobs(env):
        assert isinstance(job, ScheduledJob)
        assert job.timeout_seconds > 0
