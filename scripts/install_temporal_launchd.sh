#!/bin/sh
# Install/update a per-user weekday temporal-capture launchd job on macOS.
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This installer is for macOS launchd; use scripts/run_temporal_capture.sh from cron elsewhere." >&2
    exit 1
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
temporal_store=${1:?Usage: scripts/install_temporal_launchd.sh /absolute/temporal/store}
launch_agents_dir="$HOME/Library/LaunchAgents"
plist_path="$launch_agents_dir/com.tradingagents.temporal-capture.plist"
log_dir="$project_dir/.tradingagents"

mkdir -p "$launch_agents_dir" "$log_dir"
cat > "$plist_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tradingagents.temporal-capture</string>
  <key>ProgramArguments</key><array><string>$project_dir/scripts/run_temporal_capture.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>15</integer></dict>
  <key>EnvironmentVariables</key><dict>
    <key>TRADINGAGENTS_TEMPORAL_STORE</key><string>$temporal_store</string>
    <key>TRADINGAGENTS_COMMAND</key><string>tradingagents</string>
  </dict>
  <key>StandardOutPath</key><string>$log_dir/temporal-capture.log</string>
  <key>StandardErrorPath</key><string>$log_dir/temporal-capture.error.log</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
EOF

chmod +x "$project_dir/scripts/run_temporal_capture.sh"
launchctl bootout "gui/$(id -u)/com.tradingagents.temporal-capture" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist_path"
echo "Installed com.tradingagents.temporal-capture; inspect logs in $log_dir"
