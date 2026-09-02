#!/bin/sh
# Pull the newest store backup from the cloud trader machine to the laptop.
# The laptop is a read-only consumer: this copy serves dev, replay, and
# reports, and doubles as the off-machine backup of the canonical store.
set -eu

app=${TRADER_FLY_APP:-tradagent}
dest_dir=${TRADER_STORE_MIRROR:-"$HOME/.tradingagents/temporal-cloud-mirror"}

mkdir -p "$dest_dir"

# The trader writes a 7-slot weekday rotation; fetch the newest slot.
newest=$(flyctl ssh console -a "$app" --process-group trader -C \
    "ls -t /data/backups" 2>/dev/null | grep -E '^temporal-[0-6]\.sqlite3$' | head -1)
if [ -z "$newest" ]; then
    echo "No backup found on the trader volume yet." >&2
    exit 1
fi

tmp="$dest_dir/.incoming.sqlite3"
rm -f "$tmp"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest" "$tmp"
mv "$tmp" "$dest_dir/temporal.sqlite3"
echo "Mirrored $newest -> $dest_dir/temporal.sqlite3"
