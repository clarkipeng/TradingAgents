#!/usr/bin/env bash
#
# Deploy the media poller to Fly.io, end to end and idempotently:
#   - ensures the Fly app exists
#   - attaches Fly Managed Postgres (or uses MEDIA_DB_URL for Supabase/Neon)
#   - stages secrets (DB URL + source tokens) pulled from your environment / .env
#   - builds, deploys, and scales to exactly one machine
#   - prints collection stats
#
# Secrets are read from the shell environment first, then from .env (which is
# gitignored). Values are never printed — only key names are logged.
#
# Usage:
#   ./scripts/deploy_fly.sh [app-name]
#
# Configuration (env var or .env entry; CLI arg overrides app name):
#   FLY_APP            Fly app name (defaults to the `app` in fly.toml)
#   FLY_MPG_CLUSTER    Fly Managed Postgres cluster to attach (sets DATABASE_URL)
#   MEDIA_DB_URL       use instead of Fly MPG (e.g. Supabase/Neon connection string)
#   FLY_DEPLOY_DEPOT   pass to `fly deploy --depot` (defaults to false; use auto/true to opt in)
#   FLY_WAIT_TIMEOUT   machine start wait for `fly deploy` (defaults to 10m)
#   X_BEARER_TOKEN     optional — enables the 'x' source
#   TRUTHSOCIAL_TOKEN  optional — enables Truth Social
#
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (where fly.toml + Dockerfile.poller live)

die() { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# --- read a single KEY from .env without sourcing it (connection strings can
#     contain & ? = which a naive `source` would mangle / execute). -----------
read_dotenv() {
  local key="$1" file=".env" line val
  [ -f "$file" ] || return 1
  line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n1)" || return 1
  [ -n "$line" ] || return 1
  val="${line#*=}"
  val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"   # strip quotes
  printf '%s' "$val"
}

# Populate a variable from the environment, falling back to .env.
load() {
  local key="$1"
  if [ -z "${!key:-}" ]; then
    local v; v="$(read_dotenv "$key" || true)"
    [ -n "$v" ] && export "$key=$v"
  fi
  return 0
}

# --- preflight --------------------------------------------------------------
command -v fly >/dev/null 2>&1 || die "flyctl not found. Install: https://fly.io/docs/flyctl/install/"
fly auth whoami >/dev/null 2>&1 || die "not logged in. Run: fly auth login"
[ -f fly.toml ] || die "fly.toml not found (run from the repo root)."

for k in FLY_APP FLY_MPG_CLUSTER MEDIA_DB_URL X_BEARER_TOKEN TRUTHSOCIAL_TOKEN; do load "$k"; done

# App name: CLI arg > FLY_APP > the app already in fly.toml.
toml_app="$(grep -E '^app[[:space:]]*=' fly.toml | head -n1 | sed -E 's/^app[[:space:]]*=[[:space:]]*"?([^"]+)"?.*/\1/')"
APP="${1:-${FLY_APP:-$toml_app}}"
[ -n "$APP" ] && [ "$APP" != "tradingagents-poller" ] || \
  die "set a unique app name: ./scripts/deploy_fly.sh <app-name> (or FLY_APP=...)"

# Keep fly.toml's app field in sync so plain `fly` commands target this app.
if [ "$APP" != "$toml_app" ]; then
  info "Setting fly.toml app = \"$APP\""
  sed -i.bak -E "s/^app[[:space:]]*=.*/app = \"$APP\"/" fly.toml && rm -f fly.toml.bak
fi

# --- 1. app -----------------------------------------------------------------
if fly apps list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$APP"; then
  info "App '$APP' already exists."
else
  info "Creating app '$APP'..."
  fly apps create "$APP"
fi

# --- 2. database ------------------------------------------------------------
if [ -n "${FLY_MPG_CLUSTER:-}" ]; then
  info "Attaching Managed Postgres '$FLY_MPG_CLUSTER' (sets DATABASE_URL)..."
  fly mpg attach "$FLY_MPG_CLUSTER" --app "$APP" >/dev/null 2>&1 || \
    info "  (attach reported an issue — likely already attached; continuing)"
elif [ -n "${MEDIA_DB_URL:-}" ]; then
  info "Using MEDIA_DB_URL secret for the database (Supabase/Neon/etc.)."
else
  echo "warning: no database configured — set FLY_MPG_CLUSTER or MEDIA_DB_URL." >&2
  echo "         Without one the poller falls back to ephemeral SQLite (data lost on restart)." >&2
fi

# --- 3. secrets (staged; applied on the next deploy, no extra restart) ------
secret_args=()
for k in MEDIA_DB_URL X_BEARER_TOKEN TRUTHSOCIAL_TOKEN; do
  [ -n "${!k:-}" ] && secret_args+=("$k=${!k}")
done
if [ "${#secret_args[@]}" -gt 0 ]; then
  info "Staging secrets: ${secret_args[*]%%=*}"   # names only, never values
  fly secrets set --stage --app "$APP" "${secret_args[@]}"
else
  info "No secrets to stage from env/.env."
fi

# --- 4. deploy + scale ------------------------------------------------------
info "Deploying..."
fly deploy --app "$APP" --depot="${FLY_DEPLOY_DEPOT:-false}" \
  --ha=false --wait-timeout="${FLY_WAIT_TIMEOUT:-10m}"

info "Scaling to exactly one machine (avoid double-polling)..."
fly scale count 1 --app "$APP" --yes 2>/dev/null || fly scale count 1 --app "$APP"

# --- 5. verify --------------------------------------------------------------
info "Done. Recent collection stats:"
fly ssh console --app "$APP" -C "tradingagents-poller --stats" \
  || info "(stats not ready yet — give it a cycle, then: fly logs --app $APP)"
