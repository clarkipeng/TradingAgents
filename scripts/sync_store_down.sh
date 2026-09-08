#!/bin/sh
# Pull the newest store backup from the cloud trader machine to the laptop.
# The laptop is a read-only consumer: this copy serves dev, replay, and
# reports, and doubles as the off-machine backup of the canonical store.
set -eu

app=${TRADER_FLY_APP:-tradagent}
dest_dir=${TRADER_STORE_MIRROR:-"$HOME/.tradingagents/temporal-cloud-mirror"}

mkdir -p "$dest_dir"

# The trader publishes one atomically selected, versioned backup unit.
newest=$(flyctl ssh console -a "$app" --process-group trader -C \
    "test -s /data/backups/temporal-latest/temporal.sqlite3 && test -s /data/backups/temporal-latest/model-budget.sqlite && test -s /data/backups/temporal-latest/manifest.json && test -s /data/backups/temporal-latest/artifacts.tar && printf temporal-latest" 2>/dev/null || true)
if [ "$newest" != "temporal-latest" ]; then
    echo "No backup found on the trader volume yet." >&2
    exit 1
fi

incoming="$dest_dir.incoming-unit"
incoming_store="$dest_dir.incoming-store"
rm -rf "$incoming" "$incoming_store"
mkdir -p "$incoming"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/temporal.sqlite3" "$incoming/temporal.sqlite3"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/model-budget.sqlite" "$incoming/model-budget.sqlite"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/manifest.json" "$incoming/manifest.json"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest/artifacts.tar" "$incoming/artifacts.tar"
python3 -m tradingagents.store_backup verify-unit "$incoming"
python3 -m tradingagents.store_backup restore "$incoming" "$incoming_store"

# Keep the current mirror untouched until the complete incoming unit has been
# verified and restored.  The old directory remains recoverable if the final
# local rename fails.
old="$dest_dir.previous"
rm -rf "$old"
if [ -e "$dest_dir/temporal.sqlite3" ]; then
    mv "$dest_dir" "$old"
    mkdir -p "$dest_dir"
fi
if ! mv "$incoming_store" "$dest_dir"; then
    [ -e "$old" ] && mv "$old" "$dest_dir"
    exit 1
fi
rm -rf "$old" "$incoming"
echo "Mirrored $newest -> $dest_dir with verified manifest and budget ledger"
