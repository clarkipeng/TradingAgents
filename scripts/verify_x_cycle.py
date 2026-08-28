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

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents import poller
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


def main() -> int:
    day = (
        datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if len(sys.argv) > 1
        else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    day_start = day.timestamp()
    day_end = (day + timedelta(days=1)).timestamp()
    print(f"Verifying X capture for UTC day {day:%Y-%m-%d}")
    print(f"media store: {MEDIA_DB}")
    print(f"temporal store: {TEMPORAL_ROOT}\n")

    conn = sqlite3.connect(f"file:{MEDIA_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # 1. Terminal cycle for the day.
    cycles = conn.execute(
        "SELECT collection_cycle_id, status FROM collection_cycles "
        "WHERE cycle_kind='x-daily' AND period_key=?",
        (f"{day:%Y-%m-%d}",),
    ).fetchall()
    complete = [row for row in cycles if row["status"] == "complete"]
    check(
        "x-daily cycle terminal-complete",
        len(complete) == 1,
        f"cycles={[(row['collection_cycle_id'], row['status']) for row in cycles]}",
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
    spent_by_category = dict(conn.execute(
        "SELECT json_extract(metadata_json, '$.budget_category'), "
        "COALESCE(SUM(cost_units), 0) FROM fetch_runs "
        "WHERE json_extract(metadata_json, '$.budget_category') IS NOT NULL "
        "AND started_utc >= ? AND started_utc < ? GROUP BY 1",
        (day_start, day_end),
    ).fetchall())
    for category, cap in caps.items():
        spent = spent_by_category.get(category, 0)
        check(f"{category} spend within cap", spent <= cap, f"{spent}/{cap} units")
    unknown = set(spent_by_category) - set(caps)
    check("no uncapped budget categories", not unknown, f"unknown={sorted(unknown)}")

    # 2b. Roster coverage: every declared cashtag slot attempted today.
    roster_cycles = conn.execute(
        "SELECT status FROM collection_cycles WHERE cycle_kind='x-roster-daily' "
        "AND period_key=?",
        (f"{day:%Y-%m-%d}",),
    ).fetchall()
    roster_attempted = conn.execute(
        "SELECT COUNT(DISTINCT query_key) FROM fetch_runs WHERE provider='x' "
        "AND query_key LIKE 'cashtag:%' AND started_utc >= ? AND started_utc < ?",
        (day_start, day_end),
    ).fetchone()[0]
    roster_size = len(poller.X_ROSTER_STATIC_SLOTS)
    check(
        "roster cycle attempted every slot",
        bool(roster_cycles) and roster_attempted == roster_size,
        f"cycle={[row['status'] for row in roster_cycles]} slots={roster_attempted}/{roster_size}",
    )

    # 3. X posts landed.
    posts = conn.execute(
        "SELECT external_id, body FROM media_posts "
        "WHERE source='x' AND fetched_utc >= ? AND fetched_utc < ?",
        (day_start, day_end),
    ).fetchall()
    check("x posts in media store", len(posts) > 0, f"{len(posts)} posts")

    # 4. Mirrored into temporal documents.
    x_fetch_run_ids = [
        row["fetch_run_id"]
        for row in conn.execute(
            "SELECT fetch_run_id FROM fetch_runs WHERE provider='x' "
            "AND status='success' AND started_utc >= ? AND started_utc < ?",
            (day_start, day_end),
        )
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

    conn.close()
    print()
    if _failures:
        print(f"{len(_failures)} check(s) FAILED: {', '.join(_failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
