#!/bin/sh
# Pull the Fly collector's cloud-owned captures into the local temporal
# corpus. The cloud store (Managed Postgres) is only reachable through
# `flyctl mpg proxy`, so this script owns a short-lived tunnel for the copy.
# Imports are idempotent: rerunning a window is free, and a missed day
# costs nothing because Postgres retains everything.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file=${TRADINGAGENTS_MEDIA_ENV_FILE:-"$project_dir/.env"}
command_name=${TRADINGAGENTS_COMMAND:-"$project_dir/.venv/bin/tradingagents"}
temporal_store=${TRADINGAGENTS_TEMPORAL_STORE:-"$HOME/.tradingagents/temporal"}
cluster=${FLY_MPG_CLUSTER_ID:-9jknq03mm5no68w3}
# A fresh port per invocation: back-to-back runs on one port connect to the
# previous invocation's dying proxy and drop mid-import.
proxy_port=${FLY_MPG_PROXY_PORT:-$((16400 + $$ % 200))}
# Sources the cloud collector owns; the laptop's own poller covers the rest.
sources=${CLOUD_MEDIA_SOURCES:-x,xtrend,trendnews,globalnews,hacker_news,gdelt}
from_date=${1:-$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)}
to_date=${2:-$(date -u +%Y-%m-%d)}

remote_url=$(grep -E '^MEDIA_DB_URL_REMOTE=' "$env_file" | head -1 | cut -d= -f2-)
if [ -z "$remote_url" ]; then
    echo "MEDIA_DB_URL_REMOTE is not set in $env_file" >&2
    exit 1
fi
local_url=$(printf '%s' "$remote_url" | sed -E "s#@[^/]+/#@127.0.0.1:${proxy_port}/#")

flyctl mpg proxy "$cluster" --local-port "$proxy_port" >/dev/null 2>&1 &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null || true' EXIT INT TERM

attempts=0
until nc -z 127.0.0.1 "$proxy_port" 2>/dev/null; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 15 ]; then
        echo "mpg proxy never opened port $proxy_port" >&2
        exit 1
    fi
    sleep 2
done

exec_status=0
"$command_name" temporal-media-import \
    --from "$from_date" --to "$to_date" \
    --sources "$sources" \
    --store "$temporal_store" \
    --media-db-url "$local_url" \
    --limit 10000 || exec_status=$?
exit "$exec_status"
