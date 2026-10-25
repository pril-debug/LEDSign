#!/usr/bin/env bash
set -euo pipefail

# Paths
ROOT="$(dirname "$(readlink -f "$0")")/.."
WEB_DIR="$ROOT/web"
VENV="$ROOT/venv"

# Controller process; the dashboard runs independently under sign-web.
cd "$ROOT"
exec "$VENV/bin/python" -m boot.splash
