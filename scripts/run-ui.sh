#!/usr/bin/env bash
set -euo pipefail

# Paths
ROOT="$(dirname "$(readlink -f "$0")")/.."
WEB_DIR="$ROOT/web"
VENV="$ROOT/venv"

# Make sure venv exists
if [ ! -d "$VENV" ]; then
  echo "==> Creating Python virtualenv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip flask
fi

# Run Flask web GUI
export FLASK_APP="$WEB_DIR/app.py"
export FLASK_RUN_PORT=8000
export FLASK_RUN_HOST=0.0.0.0

echo "==> Starting LEDSign web UI at http://<pi-ip>:8000"
exec "$VENV/bin/flask" run
