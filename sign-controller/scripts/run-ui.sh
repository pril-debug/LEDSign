#!/usr/bin/env bash
set -Eeuo pipefail

PI_USER="${PI_USER:-$(id -un)}"
HOME_DIR="$(getent passwd "$PI_USER" | cut -d: -f6)"
ROOT_DIR="${HOME_DIR}/sign-controller"
: "${GIT_URL:=https://github.com/pril-debug/LEDSign.git}"   # change if needed
: "${BRANCH:=main}"

echo "==> Pulling $BRANCH from $GIT_URL"
if [ -d "$ROOT_DIR/.git" ]; then
  git -C "$ROOT_DIR" fetch --all -p
  git -C "$ROOT_DIR" checkout "$BRANCH"
  git -C "$ROOT_DIR" reset --hard "origin/${BRANCH}"
else
  rm -rf "$ROOT_DIR"
  git clone --branch "$BRANCH" --depth=1 "$GIT_URL" "$ROOT_DIR"
fi

echo "==> Python venv"
if [ ! -d "$ROOT_DIR/venv" ]; then python3 -m venv "$ROOT_DIR/venv"; fi
"$ROOT_DIR/venv/bin/pip" install --upgrade pip wheel
"$ROOT_DIR/venv/bin/pip" install pillow

echo "==> Running UI (Ctrl+C to quit)"
cd "$ROOT_DIR"
exec "$ROOT_DIR/venv/bin/python" boot/splash.py
