#!/bin/sh
# Run the media poller daemon with credentials from the project .env exported
# into the environment (the poller itself never loads .env). Token values are
# never printed; only names are reported on failure.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file=${TRADINGAGENTS_MEDIA_ENV_FILE:-"$project_dir/.env"}
command_name=${TRADINGAGENTS_POLLER_COMMAND:-"$project_dir/.venv/bin/python"}

if [ ! -r "$env_file" ]; then
    echo "Media poller env file is unreadable: $env_file" >&2
    exit 1
fi

required_names="X_BEARER_TOKEN"
missing=""
# The "|| [ -n "$line" ]" guard keeps a final line that lacks a trailing
# newline (a common .env state) from being silently dropped.
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        \#*|"") continue ;;
        *=*)
            name=${line%%=*}
            case " $required_names " in
                *" $name "*) export "$name=${line#*=}" ;;
            esac
            ;;
    esac
done < "$env_file"

for name in $required_names; do
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        missing="$missing $name"
    fi
done
if [ -n "$missing" ]; then
    echo "Missing required env names from $env_file:$missing" >&2
    exit 1
fi

# Canonical local defaults matching prior operator runs; overridable via env.
export MEDIA_DB_URL=${MEDIA_DB_URL:-"$HOME/.tradingagents/media-poller.sqlite3"}
export TRADINGAGENTS_POLLER_TEMPORAL_STORE=${TRADINGAGENTS_POLLER_TEMPORAL_STORE:-"$HOME/.tradingagents/temporal"}

if [ "$#" -eq 0 ] && [ -z "${MEDIA_POLLER_TICKERS:-}" ]; then
    universe_file="$project_dir/config/temporal-universe.txt"
    if [ ! -r "$universe_file" ]; then
        echo "No tickers given and universe file is unreadable: $universe_file" >&2
        exit 1
    fi
    tickers=$(awk '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        { gsub(/[[:space:]]/, ""); if ($0 != "" && !seen[$0]++) print }
    ' "$universe_file" | paste -sd, -)
    set -- --tickers "$tickers"
fi

exec "$command_name" -m tradingagents.poller "$@"
