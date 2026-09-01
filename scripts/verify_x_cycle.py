#!/usr/bin/env python
"""Verify one UTC day's X capture end to end. Read-only.

Checks, in order:
  1. the day's X collection cycle exists and is terminal-complete,
  2. paid request counts stay within every declared per-day cap,
  3. X posts landed in the media store,
  4. those posts were mirrored into temporal documents,
  5. temporal_search can retrieve a mirrored post at an as_of after capture.

Exit code 0 means every check passed; any failure prints the failing check
and exits 1. Usage:

    .venv/bin/python scripts/verify_x_cycle.py [YYYY-MM-DD]

Defaults to the current UTC day. Store paths follow the launcher's canonical
defaults and honor MEDIA_DB_URL / TRADINGAGENTS_POLLER_TEMPORAL_STORE.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents import poller
from tradingagents.dataflows.media_store import open_store
from tradingagents.temporal import TemporalStore

MEDIA_DB = os.environ.get(
    "MEDIA_DB_URL", str(Path.home() / ".tradingagents" / "media-poller.sqlite3")
)
TEMPORAL_ROOT = os.environ.get(
    "TRADINGAGENTS_POLLER_TEMPORAL_STORE",
    str(Path.home() / ".tradingagents" / "temporal"),
)

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        _failures.append(label)


def _store_label(url: str) -> str:
    if "://" not in url:
        return "configured local database"
    scheme = url.split("://", 1)[0].split("+", 1)[0].lower()
    return {
        "sqlite": "configured SQLite database",
        "postgres": "configured PostgreSQL database",
        "postgresql": "configured PostgreSQL database",
    }.get(scheme, "configured database")


def _read_media_rows(
    url: str, day_start: float, day_end: float,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Read the verifier's three projections without creating or migrating a store."""
    if "://" not in url or url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cycles = [dict(row) for row in conn.execute(
                "SELECT collection_cycle_id,status,cycle_kind FROM collection_cycles "
                "WHERE (cycle_kind=? OR cycle_kind=?) AND period_key=?",
                ("x-daily", "x-roster-daily", datetime.fromtimestamp(
                    day_start, timezone.utc
                ).strftime("%Y-%m-%d")),
            )]
            runs = [dict(row) for row in conn.execute(
                "SELECT fetch_run_id,provider,query_key,status,started_utc,cost_units,metadata_json "
                "FROM fetch_runs WHERE started_utc >= ? AND started_utc < ?", (day_start, day_end)
            )]
            posts = [dict(row) for row in conn.execute(
                "SELECT external_id,body FROM media_posts WHERE source='x' "
                "AND fetched_utc >= ? AND fetched_utc < ?", (day_start, day_end)
            )]
            return cycles, runs, posts
        finally:
            conn.close()

    media_store = open_store(url, auto_migrate=False)
    try:
        with media_store.engine.connect() as connection:
            from sqlalchemy import text
            cycles = [dict(row) for row in connection.execute(text(
                "SELECT collection_cycle_id,status,cycle_kind FROM collection_cycles "
                "WHERE (cycle_kind='x-daily' OR cycle_kind='x-roster-daily') "
                "AND period_key=:day"
            ), {"day": datetime.fromtimestamp(day_start, timezone.utc).strftime("%Y-%m-%d"),
                }).mappings()]
            runs = [dict(row) for row in connection.execute(text(
                "SELECT fetch_run_id,provider,query_key,status,started_utc,cost_units,metadata_json "
                "FROM fetch_runs WHERE started_utc >= :lo AND started_utc < :hi"
            ), {"lo": day_start, "hi": day_end}).mappings()]
            posts = [dict(row) for row in connection.execute(text(
                "SELECT external_id,body FROM media_posts WHERE source='x' "
                "AND fetched_utc >= :lo AND fetched_utc < :hi"
            ), {"lo": day_start, "hi": day_end}).mappings()]
            return cycles, runs, posts
    finally:
        media_store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day", nargs="?", help="UTC day (YYYY-MM-DD)")
    parser.add_argument("--media-db-url", default=MEDIA_DB,
                        help="media store URL, including a cloud PostgreSQL URL")
    args = parser.parse_args(argv)
    day = (
        datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.day
        else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    day_start = day.timestamp()
    day_end = (day + timedelta(days=1)).timestamp()
    print(f"Verifying X capture for UTC day {day:%Y-%m-%d}")
    media_db_url = args.media_db_url
    print(f"media store: {_store_label(media_db_url)}")
    print(f"temporal store: {TEMPORAL_ROOT}\n")

    cycles, runs, posts = _read_media_rows(media_db_url, day_start, day_end)

    # 1. Terminal cycle for the day.
    x_cycles = [row for row in cycles if row["cycle_kind"] == "x-daily"]
    complete = [row for row in x_cycles if row["status"] == "complete"]
    check(
        "x-daily cycle terminal-complete",
        len(complete) == 1,
        f"cycles={[(row['collection_cycle_id'], row['status']) for row in x_cycles]}",
    )

    # 2. Paid spend within every declared per-category cap. One provider name
    # can carry two categories (xtrend serves both the paid trend budget and
    # the separate shadow-trend budget), so caps bind per budget_category.
    evidence = poller.GLOBAL_EVENT_V2_PROTOCOL["evidence"]
    caps = {
        "trend": int(evidence["max_x_trend_requests_per_utc_day"]),
        "search": int(evidence["max_x_search_requests_per_utc_day"]),
        "shadow_trend": int(poller.X_SHADOW_POLICY["max_trend_requests_per_utc_day"]),
        "count": int(poller.X_SHADOW_POLICY["max_count_requests_per_utc_day"]),
        "roster": int(poller.X_ROSTER_V1_POLICY["max_requests_per_utc_day"]),
    }
    spent_by_category: dict[str, float] = {}
    for run in runs:
        metadata = json.loads(run["metadata_json"] or "{}")
        category = metadata.get("budget_category")
        if category is not None:
            spent_by_category[category] = (
                spent_by_category.get(category, 0) + (run["cost_units"] or 0)
            )
    for category, cap in caps.items():
        spent = spent_by_category.get(category, 0)
        check(f"{category} spend within cap", spent <= cap, f"{spent}/{cap} units")
    unknown = set(spent_by_category) - set(caps)
    check("no uncapped budget categories", not unknown, f"unknown={sorted(unknown)}")

    # 2b. Roster coverage: every declared cashtag slot attempted today.
    roster_cycles = [row for row in cycles if row.get("cycle_kind") == "x-roster-daily"]
    roster_attempted = len({
        run["query_key"] for run in runs
        if run["provider"] == "x" and run["query_key"].startswith("cashtag:")
    })
    roster_size = len(poller.X_ROSTER_STATIC_SLOTS)
    check(
        "roster cycle attempted every slot",
        bool(roster_cycles) and roster_attempted == roster_size,
        f"cycle={[row['status'] for row in roster_cycles]} slots={roster_attempted}/{roster_size}",
    )

    # 3. X posts landed.
    check("x posts in media store", len(posts) > 0, f"{len(posts)} posts")

    # 4. Mirrored into temporal documents.
    x_fetch_run_ids = [
        row["fetch_run_id"] for row in runs
        if row["provider"] == "x" and row["status"] == "success"
    ]
    temporal_conn = sqlite3.connect(
        f"file:{Path(TEMPORAL_ROOT) / 'temporal.sqlite3'}?mode=ro", uri=True
    )
    mirrored = 0
    for run_id in x_fetch_run_ids:
        # Fetch-run IDs are unique tokens; match them alone so the check is
        # independent of JSON separator style.
        mirrored += temporal_conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE response_json LIKE ?",
            (f"%{run_id}%",),
        ).fetchone()[0]
    temporal_conn.close()
    check(
        "x posts mirrored to temporal evidence",
        len(posts) == 0 or mirrored > 0,
        f"{mirrored} mirrored across {len(x_fetch_run_ids)} successful runs",
    )

    # 5. A mirrored post is retrievable after capture.
    if posts:
        sample = max(posts, key=lambda row: len(row["body"] or ""))
        query = " ".join((sample["body"] or "").split()[:6])
        store = TemporalStore(TEMPORAL_ROOT)
        results = store.search(query, as_of=datetime.now(timezone.utc)).results
        check(
            "temporal_search retrieves a captured x post",
            len(results) > 0,
            f"query={query!r} results={len(results)}",
        )
    else:
        check("temporal_search retrieves a captured x post", False, "no posts to sample")

    print()
    if _failures:
        print(f"{len(_failures)} check(s) FAILED: {', '.join(_failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
