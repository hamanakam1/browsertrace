#!/usr/bin/env bash
# Start the Dashboard Intelligence extension backend.
# Listens on http://127.0.0.1:7890 by default (override with EXTENSION_BACKEND_PORT).
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi

PORT="${EXTENSION_BACKEND_PORT:-7890}"
echo "Starting dashboard-extension backend on http://127.0.0.1:${PORT}"
exec python3 server.py
