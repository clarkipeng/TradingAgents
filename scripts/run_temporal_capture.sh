#!/bin/sh
# Run the full temporal capture once. Intended for cron or launchd.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
universe_file=${TRADINGAGENTS_TEMPORAL_UNIVERSE_FILE:-"$project_dir/config/temporal-universe.txt"}
temporal_store=${TRADINGAGENTS_TEMPORAL_STORE:?Set TRADINGAGENTS_TEMPORAL_STORE to the temporal corpus directory.}
command_name=${TRADINGAGENTS_COMMAND:-tradingagents}

if [ ! -r "$universe_file" ]; then
    echo "Temporal universe file is unreadable: $universe_file" >&2
    exit 1
fi

case "$(date +%u)" in
    6|7) exit 0 ;;
esac

tickers=$(awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    { gsub(/[[:space:]]/, ""); print }
' "$universe_file" | paste -sd, -)

if [ -z "$tickers" ]; then
    echo "Temporal universe is empty: $universe_file" >&2
    exit 1
fi

exec "$command_name" temporal-capture \
    --tickers "$tickers" \
    --full-surface \
    --store "$temporal_store"
