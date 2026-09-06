#!/bin/sh
# Pull the newest store backup from the cloud trader machine to the laptop.
# The laptop is a read-only consumer: this copy serves dev, replay, and
# reports, and doubles as the off-machine backup of the canonical store.
set -eu

app=${TRADER_FLY_APP:-tradagent}
dest_dir=${TRADER_STORE_MIRROR:-"$HOME/.tradingagents/temporal-cloud-mirror"}

mkdir -p "$dest_dir"

# The trader publishes one atomically replaced database plus its manifest.
newest=$(flyctl ssh console -a "$app" --process-group trader -C \
    "test -s /data/backups/temporal-latest.sqlite3 && test -s /data/backups/temporal-latest.manifest.json && test -s /data/backups/temporal-latest.artifacts.tar && printf temporal-latest.sqlite3" 2>/dev/null || true)
if [ "$newest" != "temporal-latest.sqlite3" ]; then
    echo "No backup found on the trader volume yet." >&2
    exit 1
fi

tmp="$dest_dir/.incoming.sqlite3"
manifest_tmp="$dest_dir/.incoming.manifest.json"
artifacts_tmp="$dest_dir/.incoming.artifacts.tar"
rm -f "$tmp"
rm -f "$manifest_tmp"
rm -f "$artifacts_tmp"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/$newest" "$tmp"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/temporal-latest.manifest.json" "$manifest_tmp"
flyctl ssh sftp get -a "$app" --process-group trader "/data/backups/temporal-latest.artifacts.tar" "$artifacts_tmp"
actual_sha=$(shasum -a 256 "$tmp" | awk '{print $1}')
expected_sha=$(sed -n 's/.*"database_sha256": "\([0-9a-f]*\)".*/\1/p' "$manifest_tmp")
actual_artifacts_sha=$(shasum -a 256 "$artifacts_tmp" | awk '{print $1}')
expected_artifacts_sha=$(sed -n 's/.*"artifacts_archive_sha256": "\([0-9a-f]*\)".*/\1/p' "$manifest_tmp")
if [ -z "$expected_sha" ] || [ "$actual_sha" != "$expected_sha" ] || \
   [ -z "$expected_artifacts_sha" ] || [ "$actual_artifacts_sha" != "$expected_artifacts_sha" ]; then
    echo "Backup manifest does not match database." >&2
    exit 1
fi
mv "$tmp" "$dest_dir/temporal.sqlite3"
mv "$manifest_tmp" "$dest_dir/temporal-latest.manifest.json"
mkdir -p "$dest_dir/artifacts"
tar -xf "$artifacts_tmp" -C "$dest_dir"
rm -f "$artifacts_tmp"
echo "Mirrored $newest -> $dest_dir/temporal.sqlite3 with verified manifest"
