# Hosting the media poller

The poller ([`tradingagents/poller.py`](../tradingagents/poller.py)) accumulates
social/news history into a database so you can backtest on it later. It reads
all configuration from environment variables, so it runs unchanged on a laptop,
a VM, a container platform, or a scheduled job.

## Storage: local file vs. cloud database

The store is chosen by `MEDIA_DB_URL`:

| `MEDIA_DB_URL` | Backend | Use |
|---|---|---|
| *(unset)* | SQLite at `~/.tradingagents/cache/media.db` | local dev |
| `sqlite:////data/media.db` or a bare path | SQLite | single host with a persistent volume |
| `postgresql+psycopg://user:pass@host:5432/db` | Postgres (via SQLAlchemy) | **cloud / multi-host** |

> A cloud container's local disk is ephemeral — if you host the poller in the
> cloud, use a managed database (or a mounted volume), or collected data is lost
> on restart. Networked stores need the optional extra:
> `pip install "tradingagents[poller]"` (already included in `Dockerfile.poller`).

The schema dedups on `(source, external_id)`, so multiple poller instances or
restarts never double-count.

## Database options

The store works with any Postgres via `MEDIA_DB_URL` (or `DATABASE_URL`) — you're
not locked to one provider; switching is just a different connection string.
Stay on **Postgres** (the dedup uses Postgres/SQLite `ON CONFLICT`; other engines
aren't supported). `postgres://` and `postgresql://` strings are auto-rewritten to
the psycopg driver, so provider-supplied strings work unedited.

- **Fly Managed Postgres (recommended if hosting on Fly)** — co-located with the
  poller, one platform, one bill against your Fly credit. See below.
- **Neon** — serverless Postgres, generous free tier; best standalone option.
- **Supabase** — see [Setting up Supabase](#setting-up-the-supabase-database).
- **AWS RDS / Aurora** — if you'd rather spend AWS credits.

### Fly Managed Postgres

```bash
fly mpg create                       # pick name, region (match the poller's), plan
# Attach to the poller app — this sets DATABASE_URL as a secret, which the
# store reads automatically (no MEDIA_DB_URL needed):
fly mpg attach <cluster-name> --app <your-poller-app>
```

That's it — `open_store` picks up `DATABASE_URL` and normalizes the driver. The
`media_posts` / `macro_odds` tables are created on the first poll. (Exact
`fly mpg` subcommands evolve; confirm with `fly mpg --help`.) To inspect:

```bash
fly mpg connect <cluster-name>       # psql shell
```

## Setting up the Supabase database

Supabase is managed Postgres; our store speaks it directly via `MEDIA_DB_URL`.
Steps, start to finish:

**1. Account (and YC credits).** Sign up at [supabase.com](https://supabase.com)
(GitHub login is easiest). If you have the Supabase YC deal, claim it from YC
Bookface → Deals first — credits apply at the **organization** level, so create
your project under that org and they'll cover it.

**2. Create the project.** New project → pick the org, name it (e.g.
`tradingagents`), set a strong **database password** (save it — you can't see it
again, only reset it), and choose a **region** near your Fly region (Supabase
"East US (North Virginia)" = `us-east-1` ↔ Fly `iad`). Provisioning takes ~2 min.

**3. Get the connection string.** Project → **Connect** (or Settings →
Database). Use the **Session pooler** — for a persistent hourly worker it's the
right mode: IPv4-friendly (Fly egress works without IPv6 quirks) and it supports
prepared statements (transaction mode does not, which breaks psycopg3's
defaults). Supabase shows something like:

```
postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Turn it into your `MEDIA_DB_URL` by switching the driver to psycopg and
requiring SSL:

```
postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

> Avoid the **direct** connection (`db.<ref>.supabase.co:5432`) — it's IPv6-only
> now. The **Transaction** pooler (port `6543`) is only for serverless/many
> ephemeral connections; if you ever use it with psycopg3, also append
> `&prepare_threshold=0` or you'll hit "prepared statement does not exist".

**4. Schema — nothing to do.** The poller auto-creates the `media_posts` table
and its index on first connect (`media_store` calls `create_all`). No SQL to run.

**5. Wire it up and test.** Locally (needs the extra for the Postgres driver):

```bash
pip install "tradingagents[poller]"
export MEDIA_DB_URL="postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
tradingagents-poller --tickers NVDA --once
tradingagents-poller --stats           # should show rows landing in Supabase
```

On Fly, set it as a secret instead: `fly secrets set MEDIA_DB_URL="…"`.

**6. View the data.** Supabase dashboard → **Table Editor** → `media_posts`, or
**SQL Editor**:

```sql
select source, count(*), min(to_timestamp(created_utc)), max(to_timestamp(created_utc))
from media_posts group by source order by source;
```

> Keep `MEDIA_DB_URL` secret — it contains the DB password. Use `fly secrets`
> (or a `.env` that's gitignored), never commit it. The free tier is ~500 MB; at
> ~0.2 GB/month for a handful of tickers, credits or Pro give years of runway.

## Configuration (environment variables)

| Variable | Meaning | Default |
|---|---|---|
| `MEDIA_POLLER_TICKERS` | comma-separated tickers (**required**) | — |
| `MEDIA_DB_URL` | store location (see above) | local SQLite |
| `MEDIA_POLLER_SOURCES` | subset of `stocktwits,reddit,bluesky,truthsocial,news,x` | keyless set (+`x` if token) |
| `MEDIA_POLLER_INTERVAL` | seconds between polls (daemon mode) | `3600` (hourly) |
| `MEDIA_POLLER_ONCE` | `1`/`true` → poll once and exit (for cron) | unset |
| `X_BEARER_TOKEN` | enables the `x` source (paid API) | unset |
| `TRUTHSOCIAL_TOKEN` | enables Truth Social | unset |

CLI flags override env vars; see `tradingagents-poller --help`.

## Option 1 — Docker Compose (poller + Postgres)

Self-contained stack with a local Postgres:

```bash
echo "MEDIA_POLLER_TICKERS=NVDA,MU,AAPL" >> .env      # plus any source tokens
docker compose -f docker-compose.poller.yml up -d --build
docker compose -f docker-compose.poller.yml logs -f poller
docker compose -f docker-compose.poller.yml run --rm poller --stats
```

For a **managed** database instead, delete the `db` service and set
`MEDIA_DB_URL` on the `poller` service to your provider's connection string.

## Option 1b — Fly.io worker + Managed Postgres (recommended fast path)

A stateless always-on worker on Fly that writes to a managed Postgres. Uses
[`fly.toml`](../fly.toml) + [`Dockerfile.poller`](../Dockerfile.poller). ~10
minutes end to end; the machine is ~256 MB and costs a few dollars/month.

**Scripted (recommended):** put your secrets (`X_BEARER_TOKEN`,
`TRUTHSOCIAL_TOKEN`, and `MEDIA_DB_URL` if not using Fly MPG) in `.env`, then:

```bash
FLY_MPG_CLUSTER=<cluster> ./scripts/deploy_fly.sh <your-app-name>
```

[`scripts/deploy_fly.sh`](../scripts/deploy_fly.sh) is idempotent — it ensures the
app exists, attaches Managed Postgres (or uses `MEDIA_DB_URL`), stages secrets
from your env/.env (values never printed), deploys, scales to one machine, and
prints stats. The manual steps below are equivalent if you prefer to run them
yourself.

**1. Worker app — Fly.io.** Edit `app` in `fly.toml` to a unique name and set
`primary_region` to match your DB region (e.g. `iad` for US-East). Create it:

```bash
fly apps create <your-app-name>            # or omit and let `fly launch` name it
```

**2. Database — Fly Managed Postgres.** Create a cluster in the same region and
attach it; `attach` sets `DATABASE_URL`, which the store reads automatically —
no `MEDIA_DB_URL` secret needed:

```bash
fly mpg create                             # match the app's region
fly mpg attach <cluster-name> --app <your-app-name>
```

(Using Supabase/Neon instead? Skip `fly mpg` and set the secret yourself:
`fly secrets set MEDIA_DB_URL="postgresql+psycopg://…"`.)

**3. Deploy.**

```bash
fly secrets set X_BEARER_TOKEN="..." TRUTHSOCIAL_TOKEN="..."   # optional, for those sources
fly deploy
fly scale count 1                          # exactly one machine (avoid double-polling)
fly logs                                    # watch the hourly cycles
```

Tickers come from `MEDIA_POLLER_TICKERS` in `fly.toml` (`[env]`); edit and
`fly deploy` to change them. To inspect what's been collected, run a one-off
against the same DB:

```bash
fly ssh console -C "tradingagents-poller --stats"
fly ssh console -C "tradingagents-poller --window NVDA --end 2026-06-28 --days 7"
```

## Option 2 — One container against a managed database

Works on any container host (Fly.io, Railway, Render, ECS, Cloud Run, a VM):

```bash
docker build -f Dockerfile.poller -t tradingagents-poller .
docker run -d --restart unless-stopped \
  -e MEDIA_POLLER_TICKERS=NVDA,MU,AAPL \
  -e MEDIA_DB_URL="postgresql+psycopg://USER:PASS@HOST:5432/DB" \
  tradingagents-poller
```

The container's default entrypoint is the hourly daemon; `--restart` keeps it
polling across reboots.

## Option 3 — Scheduled (cron / serverless / CI) instead of a daemon

Run one-shot polls on a schedule rather than a long-lived process. Set
`MEDIA_POLLER_ONCE=1` (or pass `--once`) and trigger hourly:

- **crontab** on a VM:
  ```
  0 * * * * MEDIA_POLLER_TICKERS=NVDA,MU MEDIA_DB_URL=postgresql+psycopg://... \
            /opt/venv/bin/tradingagents-poller --once >> /var/log/poller.log 2>&1
  ```
- **GitHub Actions** (`on: schedule: - cron: '0 * * * *'`) running
  `tradingagents-poller --once` with secrets for `MEDIA_DB_URL`/tokens.
- Any container platform's scheduled-job / cron primitive running the image with
  `MEDIA_POLLER_ONCE=1`.

> Cadence notes: StockTwits returns only the latest ~30 messages, so very active
> tickers may need sub-hourly polling to avoid gaps; Reddit's `t=week` window
> means hourly polls overlap heavily (dedup handles it). News RSS carries real
> historical `pubDate`s, so that source backfills weeks on the first poll.

## Verifying collection

```bash
tradingagents-poller --stats                          # rows per ticker/source + time span
tradingagents-poller --window NVDA --end 2026-06-28 --days 7   # look-ahead-safe backtest slice
```

`--window` returns exactly the slice a backtest at that trade date should feed
the analysts (upper bound is midnight of `--end`, so same-day intraday posts are
excluded — no look-ahead).
