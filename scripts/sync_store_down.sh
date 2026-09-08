#!/bin/sh
# Pull the newest store backup from the cloud trader machine to the laptop.
# The laptop is a read-only consumer: this copy serves dev, replay, and
# reports, and doubles as the off-machine backup of the canonical store.
set -eu

app=${TRADER_FLY_APP:-tradagent}
dest_dir=${TRADER_STORE_MIRROR:-"$HOME/.tradingagents/temporal-cloud-mirror"}

mkdir -p "$(dirname "$dest_dir")"

# The trader publishes one atomically selected, versioned backup unit.
newest=$(flyctl ssh console -a "$app" --process-group trader -C \
    "test -s /data/backups/temporal-latest/temporal.sqlite3 && test -s /data/backups/temporal-latest/model-budget.sqlite && test -s /data/backups/temporal-latest/manifest.json && test -s /data/backups/temporal-latest/artifacts.tar && printf temporal-latest" 2>/dev/null || true)
if [ "$newest" != "temporal-latest" ]; then
    echo "No backup found on the trader volume yet." >&2
    exit 1
fi

incoming=$(mktemp -d "$dest_dir.incoming-unit.XXXXXX")
trap 'rm -rf "$incoming"' EXIT HUP INT TERM
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/temporal.sqlite3" "$incoming/temporal.sqlite3"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/model-budget.sqlite" "$incoming/model-budget.sqlite"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/manifest.json" "$incoming/manifest.json"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/artifacts.tar" "$incoming/artifacts.tar"
python3 -m tradingagents.store_backup mirror "$incoming" "$dest_dir"
echo "Mirrored $newest -> $dest_dir with verified manifest and budget ledger"
