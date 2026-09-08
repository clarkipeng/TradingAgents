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
import json
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
SCHEDULE_LEDGER_NAME = "schedule-outcomes.json"
CHILD_STOP_GRACE_SECONDS = 30.0


@dataclass(frozen=True)
class JobResult:
    """Sanitized result of one scheduled attempt."""

    status: str
    code: str

    @property
    def successful(self) -> bool:
        return self.status == "completed"


def verify_local_p6_evidence(path: Path) -> JobResult:
    """Fail closed unless local cutover, rollback, and restore proof exists.

    This is deliberately an evidence reader, not a deployment driver.  It
    cannot turn an absent observation into permission to mutate production.
    """
    try:
        evidence = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return JobResult("failed", "evidence_unavailable")
    if not isinstance(evidence, dict):
        return JobResult("failed", "evidence_invalid")
    required = {
        "preflight": "passed",
        "cutover": "verified",
        "rollback": "verified",
        "backup_restore": "verified",
        "providers": "disabled",
    }
    if any(evidence.get(key) != value for key, value in required.items()):
        return JobResult("failed", "evidence_incomplete")
    if not isinstance(evidence.get("observed_at"), str) or not evidence["observed_at"]:
        return JobResult("failed", "evidence_timestamp_missing")
    return JobResult("completed", "p6_local_gate_verified")


class ScheduleLedger:
    """Small durable scheduler ledger owned by the supervisor process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.outcomes: dict[str, dict[str, str]] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text())
                if isinstance(payload, dict) and isinstance(payload.get("outcomes"), dict):
                    self.outcomes = {
                        str(key): value for key, value in payload["outcomes"].items()
                        if isinstance(value, dict) and isinstance(value.get("status"), str)
                    }
            except (OSError, json.JSONDecodeError):
                print("[supervisor] schedule ledger unreadable code=ledger_corrupt", flush=True)

    @staticmethod
    def key(job: ScheduledJob, slot: datetime) -> str:
        return f"{job.name}|{slot.date().isoformat()}"

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(self.path.suffix + ".partial")
        partial.write_text(json.dumps({"schema_version": 1, "outcomes": self.outcomes}, sort_keys=True))
        partial.replace(self.path)

    def recover_running(self) -> None:
        changed = False
        for outcome in self.outcomes.values():
            if outcome.get("status") == "running":
                outcome.update(status="pending", code="interrupted")
                changed = True
        if changed:
            self._save()

    def mark(self, job: ScheduledJob, slot: datetime, status: str, code: str) -> None:
        self.outcomes[self.key(job, slot)] = {
            "job": job.name,
            "logical_date": slot.date().isoformat(),
            "status": status,
            "code": code,
        }
        # Keep a bounded history while retaining all recent logical dates.
        if len(self.outcomes) > 500:
            keep = sorted(self.outcomes)[-400:]
            self.outcomes = {key: self.outcomes[key] for key in keep}
        self._save()


def _stop_process_group(process: subprocess.Popen, *, grace: float = CHILD_STOP_GRACE_SECONDS) -> None:
    """Stop and reap a child plus descendants, including timeout paths."""
    if process.poll() is not None:
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _process_tree_rss_mb(root_pid: int) -> int | None:
    """Best-effort instantaneous RSS for a process tree on Linux."""
    proc_root = Path("/proc")
    if not proc_root.exists():
        return None
    parents: dict[int, int] = {}
    rss: dict[int, int] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text()
            parent = next(line for line in status.splitlines() if line.startswith("PPid:"))
            resident = next(line for line in status.splitlines() if line.startswith("VmRSS:"))
            pid = int(entry.name)
            parents[pid] = int(parent.split()[1])
            rss[pid] = int(resident.split()[1])
        except (OSError, StopIteration, ValueError):
            continue
    members = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in members and pid not in members:
                members.add(pid)
                changed = True
    return max(0, sum(rss.get(pid, 0) for pid in members) // 1024)


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
    jobs: list[ScheduledJob], now: datetime, fired: set[tuple[str, str]] | Mapping[str, Mapping[str, str]]
) -> list[tuple[ScheduledJob, datetime]]:
    """Jobs whose current slot has not fired yet, in table order."""
    due: list[tuple[ScheduledJob, datetime]] = []
    for job in jobs:
        slot = job.slot_for(now)
        if slot is None:
            continue
        key = (job.name, slot.isoformat())
        durable_key = f"{job.name}|{slot.date().isoformat()}"
        already_done = (
            key in fired
            if isinstance(fired, set)
            else fired.get(durable_key, {}).get("status") in {"completed", "running", "failed"}
        )
        if not already_done:
            due.append((job, slot))
    return due


def backup_store(
    store_dir: Path,
    backup_dir: Path,
    *,
    artifact_paths: list[Path] | None = None,
) -> Path:
    """Publish one verified backup unit; retained for supervisor callers."""
    from tradingagents.store_backup import backup_store as publish_backup

    if artifact_paths not in (None, [store_dir / "artifacts"]):
        raise ValueError("custom artifact roots are no longer supported")
    return publish_backup(store_dir, backup_dir)


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
            target = backup_store(
                Path(store),
                Path(store).parent / "backups",
                artifact_paths=[Path(store) / "artifacts"],
            )
            print(f"[supervisor] backup wrote {target}", flush=True)

        return run

    jobs = [
        ScheduledJob("temporal-capture", 17, 15, True, 3000.0, capture),
        ScheduledJob("portfolio-day", 17, 45, True, 9000.0, portfolio_day),
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
            and key not in {
                "MEDIA_HEALTH_PORT",
                "TRADINGAGENTS_ALERT_WEBHOOK_URL",
                "MEDIA_REQUIRE_ALERT_WEBHOOK",
                "MEDIA_COLLECTION_ENABLED",
            }
        }
        self.child_env.update(
            {
                "MEDIA_DB_URL": str(store.parent / "media-poller.sqlite3"),
                "TRADINGAGENTS_POLLER_TEMPORAL_STORE": str(store),
                "MEDIA_POLLER_SOURCES": env.get(
                    "TRADER_MEDIA_SOURCES", "stocktwits,reddit,news"
                ),
                "MEDIA_POLLER_TRADING_HOURS": "true",
                "MEDIA_POLLER_ALLOW_MACRO": "false",
                "MEDIA_POLLER_PAUSE_POLICY": "ticker",
                "MEDIA_POLLER_HEALTH_ENDPOINT": "disabled",
                "MEDIA_POLLER_ALERT_IDENTITY": "ticker-poller",
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
        self.process = subprocess.Popen(
            self.argv,
            env=self.child_env,
            start_new_session=True,
        )
        self.started_at = now_monotonic

    def terminate(self) -> None:
        if self.process is not None and self.process.poll() is None:
            _stop_process_group(self.process)
        elif self.process is not None:
            self.process.wait()


def run_job(job: ScheduledJob, slot: datetime) -> JobResult:
    built = job.build(slot)
    print(f"[supervisor] firing {job.name} for slot {slot.isoformat()}", flush=True)
    if callable(built):
        try:
            built()
            print(f"[supervisor] {job.name} ok", flush=True)
            return JobResult("completed", "ok")
        except Exception as exc:  # noqa: BLE001 - the schedule must survive any job
            print(
                f"[supervisor] {job.name} failed code=callable_failure "
                f"type={type(exc).__name__}",
                flush=True,
            )
            return JobResult("failed", "callable_failure")
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(built, start_new_session=True)
        deadline = time.monotonic() + job.timeout_seconds
        peak_mb: int | None = None
        while True:
            sample = _process_tree_rss_mb(process.pid)
            if sample is not None:
                peak_mb = max(peak_mb or 0, sample)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(built, job.timeout_seconds)
            try:
                result = process.wait(timeout=min(1.0, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        print(
            f"[supervisor] {job.name} exit={result} "
            f"process_tree_peak_rss_mb={peak_mb if peak_mb is not None else 'unavailable'}",
            flush=True,
        )
        return JobResult("completed" if result == 0 else "failed", "ok" if result == 0 else "child_nonzero")
    except subprocess.TimeoutExpired:
        if process is not None:
            _stop_process_group(process)
        print(f"[supervisor] {job.name} failed code=deadline", flush=True)
        return JobResult("failed", "deadline")
    except Exception as exc:  # noqa: BLE001 - the schedule must survive any job
        if process is not None:
            _stop_process_group(process)
        print(
            f"[supervisor] {job.name} failed code=launch_failure "
            f"type={type(exc).__name__}",
            flush=True,
        )
        return JobResult("failed", "launch_failure")


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
    ledger = ScheduleLedger(Path(env.get("TRADINGAGENTS_TEMPORAL_STORE", "/data/temporal")) / SCHEDULE_LEDGER_NAME)
    ledger.recover_running()
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
        for job, slot in due_jobs(jobs, now, ledger.outcomes):
            ledger.mark(job, slot, "running", "started")
            result = run_job(job, slot)
            ledger.mark(job, slot, result.status, result.code)
            if stopping:
                break
        time.sleep(TICK_SECONDS)

    print("[supervisor] stopping; terminating poller", flush=True)
    poller.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
