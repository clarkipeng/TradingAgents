# Luna ops handoff

Date: 2026-09-06
Base: `4d1fafd`
Branch: `cdt/a87f-ta-luna-ops`

## Implemented slices

- `8b20cd1` — supervised children start in their own process groups; timeout and
  shutdown terminate, escalate, and reap the whole group. Job outcomes use
  fixed codes instead of exception text. A per-job Linux `/proc` process-tree
  RSS high-water sample replaces cumulative `RUSAGE_CHILDREN` reporting.
- `5d2ab3f` — durable JSON schedule outcomes with atomic writes, interrupted
  `running` recovery, logical-date keys, and bounded retention; versioned
  database/artifact manifest plus artifact tar archive; offsite sync uses the
  single published filename and verifies both hashes before install; ticker
  poller child receives explicit source/macro/pause/health/alert scope; failed
  post-cycle hooks retry independently without repeating the completed provider
  sweep.
- `92b8576` — per-job process-tree memory sampling correction (C4).

## Verification

- `uv run --with pytest pytest -q tests/test_cloud_supervisor.py tests/test_poller_window.py`
  — **113 passed**.
- Direct ledger/manifest/archive smoke check — **passed**.
- `python3 -m py_compile tradingagents/cloud_supervisor.py tradingagents/poller.py`
  — **passed**.
- `sh -n scripts/sync_store_down.sh` and `git diff --check` — **passed**.
- No live infrastructure, provider, Fly, or deployment actions were run.

## Remaining contract requests

1. `daily-discovery` needs an explicit `--date YYYY-MM-DD` (or equivalent
   environment contract) so the supervisor can pass the logical schedule date;
   no CLI files were changed here. The current command has no date interface.
2. The portfolio runner/CLI needs a durable typed day-outcome reader and exit
   contract (`completed`, `already_completed`, `failed`, `interrupted`,
   `ownership_contended`) so supervisor outcomes can prove sealing rather than
   relying only on child exit status.
3. The temporal backup producer needs the authoritative artifact root and any
   required capture/budget-ledger roots. The implementation currently requires
   `<store>/artifacts` for the production backup job and records hashes; the
   restore verifier should additionally validate every listed artifact after
   extraction.
4. Poller alert delivery needs an explicit identity parameter in the alert
   interface. The capability is carried as `MEDIA_POLLER_ALERT_IDENTITY`, but
   existing alert calls still use their established component identity.

Working tree is clean after the commits above; `doing.md` and excluded data,
portfolio, replay, model, store, and retriever paths were not touched.
