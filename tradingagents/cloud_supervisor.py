"""Cloud supervisor: the single process that owns the trading machine.

Runs on the Fly `trader` machine, which mounts the canonical temporal store.
It keeps the continuous market-hours media poller alive and fires the daily
jobs (capture, portfolio day, discovery, cloud-media import, store backup) at
their scheduled local times. One supervisor per store preserves the
single-mutator invariant: every writer on this machine is either this process
or a child it runs sequentially.

Scheduled jobs are idempotent or claim-guarded, so a restart that re-fires a
slot inside the grace window is harmless: a completed portfolio day refuses a
second claim, imports and discovery re-run for free, and backups overwrite.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEDULE_TZ = ZoneInfo("America/Los_Angeles")

# A job fires once per calendar slot, any time within the grace window after
# its scheduled minute. The window is wide because deploys restart this
# process; idempotency makes a re-fire safe while a narrow window would make a
# badly-timed deploy silently skip a trading day.
SLOT_GRACE = timedelta(hours=1)

POLLER_RESTART_BACKOFF_START = 30.0
POLLER_RESTART_BACKOFF_CAP = 600.0
POLLER_HEALTHY_RESET = 1800.0

TICK_SECONDS = 20.0


def universe_tickers(universe_file: Path) -> str:
    """The frozen ticker universe as a comma-joined string."""
    tickers: list[str] = []
    seen: set[str] = set()
    for line in universe_file.read_text().splitlines():
        ticker = "".join(line.split())
        if not ticker or ticker.startswith("#"):
            continue
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    if not tickers:
        raise ValueError(f"Temporal universe is empty: {universe_file}")
    return ",".join(tickers)


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    hour: int
    minute: int
    weekdays_only: bool
    timeout_seconds: float
    # Receives the slot's local date so date-parameterized jobs label
    # themselves with the slot they fire for, not the wall clock at exec time.
    build: Callable[[datetime], list[str] | Callable[[], None]]

    def slot_for(self, now: datetime) -> datetime | None:
        """The slot datetime this job should fire for at `now`, if any."""
        slot = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if not slot <= now < slot + SLOT_GRACE:
            return None
        if self.weekdays_only and slot.weekday() >= 5:
            return None
        return slot


def due_jobs(
    jobs: list[ScheduledJob], now: datetime, fired: set[tuple[str, str]]
) -> list[tuple[ScheduledJob, datetime]]:
    """Jobs whose current slot has not fired yet, in table order."""
    due: list[tuple[ScheduledJob, datetime]] = []
    for job in jobs:
        slot = job.slot_for(now)
        if slot is None:
            continue
        key = (job.name, slot.isoformat())
        if key not in fired:
            due.append((job, slot))
    return due


def backup_store(store_dir: Path, backup_dir: Path) -> Path:
    """Vacuum the store database into a single overwritten backup slot.

    One slot, not a rotation: full copies of a multi-GB store filled the
    volume in three days. History comes from Fly's scheduled volume
    snapshots (5-day retention); the laptop's nightly pull is the offsite
    copy. This file exists so both have one consistent artifact to take.
    """
    source = store_dir / "temporal.sqlite3"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / "temporal-latest.sqlite3"
    partial = backup_dir / "temporal-latest.sqlite3.partial"
    partial.unlink(missing_ok=True)
    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=300)
    try:
        conn.execute("VACUUM INTO ?", (str(partial),))
    finally:
        conn.close()
    partial.replace(target)
    return target


def build_jobs(env: Mapping[str, str]) -> list[ScheduledJob]:
    store = env.get("TRADINGAGENTS_TEMPORAL_STORE", "/data/temporal")
    universe_file = Path(
        env.get(
            "TRADINGAGENTS_TEMPORAL_UNIVERSE_FILE",
            "/home/appuser/app/config/temporal-universe.txt",
        )
    )
    tickers = universe_tickers(universe_file)
    cloud_sources = env.get(
        "CLOUD_MEDIA_SOURCES", "x,xtrend,trendnews,globalnews,hacker_news,gdelt"
    )

    def capture(slot: datetime) -> list[str]:
        return [
            "tradingagents",
            "temporal-capture",
            "--tickers",
            tickers,
            "--full-surface",
            "--store",
            store,
        ]

    def portfolio_day(slot: datetime) -> list[str]:
        return [
            "tradingagents",
            "temporal-portfolio-run",
            "--tickers",
            tickers,
            "--date",
            slot.strftime("%Y-%m-%d"),
            "--store",
            store,
        ]

    def discovery(slot: datetime) -> list[str]:
        return [
            "tradingagents",
            "temporal-daily-discovery",
            "--tickers",
            tickers,
            "--store",
            store,
        ]

    def media_import(slot: datetime) -> list[str]:
        media_db_url = env["MEDIA_DB_URL"]
        day = slot.strftime("%Y-%m-%d")
        yesterday = (slot - timedelta(days=1)).strftime("%Y-%m-%d")
        return [
            "tradingagents",
            "temporal-media-import",
            "--from",
            yesterday,
            "--to",
            day,
            "--sources",
            cloud_sources,
            "--store",
            store,
            "--media-db-url",
            media_db_url,
            "--limit",
            "10000",
        ]

    def backup(slot: datetime) -> Callable[[], None]:
        def run() -> None:
            target = backup_store(Path(store), Path(store).parent / "backups")
            print(f"[supervisor] backup wrote {target}", flush=True)

        return run

    jobs = [
        ScheduledJob("temporal-capture", 17, 15, True, 3000.0, capture),
        ScheduledJob("portfolio-day", 17, 45, True, 7200.0, portfolio_day),
        ScheduledJob("daily-discovery", 18, 30, False, 3600.0, discovery),
        ScheduledJob("cloud-media-import", 19, 15, False, 3600.0, media_import),
        ScheduledJob("store-backup", 20, 30, False, 3600.0, backup),
    ]
    # Operator pause switch for the trading day only - capture never pauses.
    # Used while a manual chain (e.g. a backfill) owns portfolio state, so a
    # scheduled day can never trade from a mid-chain position.
    if env.get("TRADER_PORTFOLIO_DAY_ENABLED", "true").lower() == "false":
        print("[supervisor] portfolio-day is paused via TRADER_PORTFOLIO_DAY_ENABLED", flush=True)
        jobs = [job for job in jobs if job.name != "portfolio-day"]
    return jobs


class PollerChild:
    """Keeps the continuous media poller alive with capped backoff."""

    def __init__(self, env: Mapping[str, str]) -> None:
        universe_file = Path(
            env.get(
                "TRADINGAGENTS_TEMPORAL_UNIVERSE_FILE",
                "/home/appuser/app/config/temporal-universe.txt",
            )
        )
        store = Path(env.get("TRADINGAGENTS_TEMPORAL_STORE", "/data/temporal"))
        self.argv = [
            sys.executable,
            "-m",
            "tradingagents.poller",
            "--tickers",
            universe_tickers(universe_file),
        ]
        # The machine-wide environment belongs to the X collector process:
        # MEDIA_DB_URL addresses its cloud Postgres (singleton lease) and
        # MEDIA_POLLER_* describes its X-only cadence. Inheriting either would
        # make this child a second X spender writing to the wrong store, so the
        # ticker poller's identity is pinned here, not inherited.
        self.child_env = {
            key: value
            for key, value in env.items()
            if not key.startswith("MEDIA_POLLER_")
        }
        self.child_env.update(
            {
                "MEDIA_DB_URL": str(store.parent / "media-poller.sqlite3"),
                "TRADINGAGENTS_POLLER_TEMPORAL_STORE": str(store),
                "MEDIA_POLLER_SOURCES": env.get(
                    "TRADER_MEDIA_SOURCES", "stocktwits,reddit,news"
                ),
                "MEDIA_POLLER_TRADING_HOURS": "true",
            }
        )
        self.process: subprocess.Popen | None = None
        self.backoff = POLLER_RESTART_BACKOFF_START
        self.restart_at = 0.0
        self.started_at = 0.0

    def next_backoff(self, healthy_seconds: float) -> float:
        if healthy_seconds >= POLLER_HEALTHY_RESET:
            self.backoff = POLLER_RESTART_BACKOFF_START
        current = self.backoff
        self.backoff = min(self.backoff * 2, POLLER_RESTART_BACKOFF_CAP)
        return current

    def ensure_running(self, now_monotonic: float) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if self.process is not None:
            healthy = now_monotonic - self.started_at
            wait = self.next_backoff(healthy)
            print(
                f"[supervisor] poller exited code={self.process.returncode}; "
                f"restarting in {wait:.0f}s",
                flush=True,
            )
            self.process = None
            self.restart_at = now_monotonic + wait
        if now_monotonic < self.restart_at:
            return
        print(f"[supervisor] starting poller: {' '.join(self.argv)}", flush=True)
        self.process = subprocess.Popen(self.argv, env=self.child_env)
        self.started_at = now_monotonic

    def terminate(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.kill()


def run_job(job: ScheduledJob, slot: datetime) -> None:
    built = job.build(slot)
    print(f"[supervisor] firing {job.name} for slot {slot.isoformat()}", flush=True)
    if callable(built):
        try:
            built()
            print(f"[supervisor] {job.name} ok", flush=True)
        except Exception as exc:  # noqa: BLE001 - the schedule must survive any job
            print(f"[supervisor] {job.name} failed: {exc!r}", flush=True)
        return
    try:
        result = subprocess.run(built, timeout=job.timeout_seconds)
        print(f"[supervisor] {job.name} exit={result.returncode}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"[supervisor] {job.name} timed out after {job.timeout_seconds}s", flush=True)
    except Exception as exc:  # noqa: BLE001 - the schedule must survive any job
        print(f"[supervisor] {job.name} failed to launch: {exc!r}", flush=True)


def store_is_seeded(env: Mapping[str, str]) -> bool:
    """Whether the canonical store exists on the volume.

    A fresh volume must be seeded explicitly (upload + restart). Implicitly
    initializing an empty canonical store would silently fork the corpus, so
    the supervisor idles instead - visibly - until the database appears.
    """
    store = Path(env.get("TRADINGAGENTS_TEMPORAL_STORE", "/data/temporal"))
    return (store / "temporal.sqlite3").exists()


def main() -> int:
    env = os.environ
    while not store_is_seeded(env):
        print(
            "[supervisor] canonical store not seeded yet; idling. "
            "Upload the store to the volume and restart.",
            flush=True,
        )
        time.sleep(60)
    jobs = build_jobs(env)
    poller = PollerChild(env)
    fired: set[tuple[str, str]] = set()
    stopping = False

    def handle_term(signum, frame) -> None:  # noqa: ANN001
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGINT, handle_term)

    print(
        f"[supervisor] up; schedule tz={SCHEDULE_TZ.key}, "
        f"jobs={[job.name for job in jobs]}",
        flush=True,
    )
    while not stopping:
        poller.ensure_running(time.monotonic())
        now = datetime.now(tz=SCHEDULE_TZ)
        for job, slot in due_jobs(jobs, now, fired):
            fired.add((job.name, slot.isoformat()))
            run_job(job, slot)
            if stopping:
                break
        # Keep the fired set from growing without bound across long uptimes.
        if len(fired) > 1000:
            cutoff = (now - timedelta(days=2)).isoformat()
            fired = {key for key in fired if key[1] >= cutoff}
        time.sleep(TICK_SECONDS)

    print("[supervisor] stopping; terminating poller", flush=True)
    poller.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
