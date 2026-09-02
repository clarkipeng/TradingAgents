#!/bin/sh
# Run one sealed paper-trading portfolio day. Intended for launchd after the
# daily tool-tape capture. Weekends exit quietly: no session, no day.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
universe_file=${TRADINGAGENTS_TEMPORAL_UNIVERSE_FILE:-"$project_dir/config/temporal-universe.txt"}
temporal_store=${TRADINGAGENTS_TEMPORAL_STORE:-"$HOME/.tradingagents/temporal"}
command_name=${TRADINGAGENTS_COMMAND:-"$project_dir/.venv/bin/tradingagents"}

case "$(date +%u)" in
    6|7) exit 0 ;;
esac

if [ ! -r "$universe_file" ]; then
    echo "Universe file is unreadable: $universe_file" >&2
    exit 1
fi

tickers=$(awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    { gsub(/[[:space:]]/, ""); if ($0 != "" && !seen[$0]++) print }
' "$universe_file" | paste -sd, -)

# The console entrypoint loads .env from the working directory.
cd "$project_dir"
exec "$command_name" temporal-portfolio-run \
    --tickers "$tickers" \
    --date "$(date +%Y-%m-%d)" \
    --store "$temporal_store"
