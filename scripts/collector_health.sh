#!/bin/sh
# One-glance collector health: daemon liveness, running build vs worktree
# build, and the last day's alert/critical log lines. Exits nonzero when the
# daemon is down, serves a stale build, or logged a critical failure.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
log_file="$project_dir/.tradingagents/media-poller.error.log"
label="com.tradingagents.media-poller"
status=0

if launchctl list "$label" >/dev/null 2>&1 \
        && [ -n "$(launchctl list | awk -v l="$label" '$3 == l && $1 != "-" {print $1}')" ]; then
    echo "PASS  daemon running ($label)"
else
    echo "FAIL  daemon not running ($label)"
    status=1
fi

worktree_build=$("$project_dir/.venv/bin/python" -c \
    'from tradingagents.research_protocol import build_identity; print(build_identity())')
running_build=$(grep -o 'build: build_[0-9a-f]*' "$log_file" 2>/dev/null | tail -1 | cut -d' ' -f2 || true)
if [ -z "$running_build" ]; then
    echo "WARN  running build unknown (daemon predates build logging; restart it)"
elif [ "$running_build" = "$worktree_build" ]; then
    echo "PASS  running build matches worktree ($worktree_build)"
else
    echo "FAIL  running build $running_build != worktree $worktree_build (restart the daemon)"
    status=1
fi

if [ -r "$log_file" ]; then
    cutoff=$(date -u -v-24H '+%Y-%m-%d %H:%M' 2>/dev/null || date -u -d '24 hours ago' '+%Y-%m-%d %H:%M')
    recent=$(awk -v cutoff="$cutoff" '$1" "$2 >= cutoff' "$log_file")
    alerts=$(printf '%s\n' "$recent" | grep -c 'alert:' || true)
    criticals=$(printf '%s\n' "$recent" | grep -c 'CRITICAL' || true)
    echo "INFO  last 24h: $alerts alert line(s), $criticals critical line(s)"
    printf '%s\n' "$recent" | grep -E 'alert:|CRITICAL' | tail -5 | sed 's/^/      /' || true
    if [ "$criticals" -gt 0 ]; then
        status=1
    fi
else
    echo "WARN  no poller log at $log_file"
fi

exit "$status"
