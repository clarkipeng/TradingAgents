#!/bin/sh
# Container entrypoint dispatch. The default (no args, or poller flags) keeps
# the historical behavior: exec the collector. The single word `supervisor`
# selects the trading-machine supervisor, which owns the volume-mounted
# temporal store and every writer that touches it.
set -eu

if [ "${1:-}" = "supervisor" ]; then
    exec python -m tradingagents.cloud_supervisor
fi
exec tradingagents-poller "$@"
