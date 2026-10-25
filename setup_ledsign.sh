#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "[setup] ERROR at line $LINENO"; exit 1' ERR

echo "==> LED Sign: starting install"

# Preserve the installed network stack. Never switch managers during installation.
# This website supports dhcpcd + wpa_supplicant on the inspected device.

# Lynx TCP port (FinishLynx Scoreboard -> Network connect)
LYNX_PORT="${LYNX_PORT:-2001}"

# -------- basics / vars --------
if [ -z "${PI_USER:-}" ]; then
  if id -u pi >/dev/null 2>&1; then PI_USER=pi; else PI_USER="$(whoami)"; fi
fi
HOME_DIR="$(getent passwd "$PI_USER" | cut -d: -f6)"
ROOT_DIR="${HOME_DIR}/sign-controller"
WEB_DIR="${ROOT_DIR}/web"
CONF_DIR="${ROOT_DIR}/config"
SCRIPTS_DIR="${ROOT_DIR}/scripts"
PY="${ROOT_DIR}/venv/bin/python"
PIP="${ROOT_DIR}/venv/bin/pip"

# Repos
APP_REPO_URL="https://github.com/pril-debug/LEDSign.git"
APP_REPO_BRANCH="main"
WEB_REPO_URL="https://github.com/pril-debug/LEDSign_Site.git"
WEB_REPO_BRANCH="main"

# Wi-Fi helper scripts (define early so sudoers can reference them)
WIFI_SCAN="${SCRIPTS_DIR}/wifi_scan.sh"
WIFI_APPLY="${SCRIPTS_DIR}/apply_wifi.sh"

# -------- time & apt --------
echo "==> Syncing time / base system"
sudo timedatectl set-ntp true || true
# Set the correct regulatory country separately during supervised OS setup.

echo "==> APT deps"
sudo apt-get update -y
PKGS=(
  build-essential git curl jq
  python3 python3-venv python3-dev python3-pip
  libjpeg-dev libpng-dev libfreetype6-dev pkg-config
  libtiff-dev libopenblas-dev cython3
  nginx imagemagick
  openresolv
  iw wpasupplicant rfkill
)
sudo apt-get install -y "${PKGS[@]}"
echo "==> Network manager preserved; see setup.txt for supervised commissioning"

# -------- project layout --------
echo "==> Creating project directories"
mkdir -p "$ROOT_DIR" "$CONF_DIR" "$SCRIPTS_DIR"

# -------- clone/update app repo (LEDSign) --------
mkdir -p "$(dirname "$ROOT_DIR")"
if [ -d "${ROOT_DIR}/.git" ]; then
  echo "==> Updating app repo"
  ( cd "$ROOT_DIR" && git fetch origin "$APP_REPO_BRANCH" && git merge --ff-only "origin/${APP_REPO_BRANCH}" )
elif [ ! -d "$ROOT_DIR" ] || [ -z "$(ls -A "$ROOT_DIR" 2>/dev/null)" ]; then
  echo "==> Cloning app repo: ${APP_REPO_URL} (${APP_REPO_BRANCH})"
  git clone --branch "$APP_REPO_BRANCH" --depth 1 "$APP_REPO_URL" "$ROOT_DIR"
else
  echo "==> Converting existing directory to repo checkout"
  ( cd "$ROOT_DIR" \
    && git init \
    && (git remote add origin "$APP_REPO_URL" 2>/dev/null || git remote set-url origin "$APP_REPO_URL") \
    && git fetch origin "$APP_REPO_BRANCH" \
    && git merge --ff-only "origin/${APP_REPO_BRANCH}" )
fi

# now create subdirs that live alongside the repo contents
mkdir -p "$CONF_DIR" "$SCRIPTS_DIR"

# Sanity: require expected directories; fail hard if missing
for p in "boot/splash.py" "modes" "scripts"; do
  [ -e "${ROOT_DIR}/${p}" ] || { echo "Missing ${p} in app repo; aborting."; exit 1; }
done

# -------- rgb-matrix lib (optional) --------
if [ ! -d "${ROOT_DIR}/ledlib" ]; then
  echo "==> Cloning hzeller/rpi-rgb-led-matrix"
  if git clone https://github.com/hzeller/rpi-rgb-led-matrix.git "${ROOT_DIR}/ledlib"; then
    make -C "${ROOT_DIR}/ledlib/lib"
    make -C "${ROOT_DIR}/ledlib/examples-api-use"
    make -C "${ROOT_DIR}/ledlib/bindings/python"
    ( cd "${ROOT_DIR}/ledlib/bindings/python" && sudo python3 setup.py install )
  else
    echo "!! Could not clone hzeller/rpi-rgb-led-matrix (network issue)."
    exit 1
  fi
fi

# -------- python venv --------
if [ ! -d "${ROOT_DIR}/venv" ]; then
  echo "==> Python venv + packages"
  python3 -m venv "${ROOT_DIR}/venv"
fi
"${PIP}" install --upgrade pip wheel
# base deps for the web ui and common utils
"${PIP}" install flask waitress pillow werkzeug

# -------- clone/update web repo (LEDSign_Site) -> ${WEB_DIR} --------
if [ ! -d "${WEB_DIR}/.git" ]; then
  echo "==> Cloning web GUI: ${WEB_REPO_URL} (${WEB_REPO_BRANCH})"
  [ ! -e "${WEB_DIR}" ] || { echo "Existing non-repository web directory: preserve and review manually"; exit 1; }
  git clone --branch "$WEB_REPO_BRANCH" --depth 1 "$WEB_REPO_URL" "$WEB_DIR"
else
  echo "==> Updating web GUI"
  ( cd "$WEB_DIR" && git fetch origin "$WEB_REPO_BRANCH" && git merge --ff-only "origin/${WEB_REPO_BRANCH}" )
fi

# -------- configuration (settings.json + logos) --------
SETTINGS_JSON="${CONF_DIR}/settings.json"
if [ ! -f "$SETTINGS_JSON" ]; then
  echo "==> Writing default settings.json"
  DEFAULT_PASS="${DEFAULT_ADMIN_PASSWORD:-admin}"
  VENV_BIN="${ROOT_DIR}/venv/bin"
  ADMIN_HASH="$(DEFAULT_PASS="$DEFAULT_PASS" "${VENV_BIN}/python" - <<'PY'
import os
from werkzeug.security import generate_password_hash
print(generate_password_hash(os.environ["DEFAULT_PASS"]))
PY
)"
  install -d -m 0755 "${CONF_DIR}"
  cat > "${SETTINGS_JSON}" <<JSON
{
  "led_rows": 64,
  "led_cols": 64,
  "led_chain": 2,
  "led_parallel": 1,
  "led_pwm_bits": 11,
  "led_brightness": 80,
  "led_gpio_slowdown": 2,
  "led_hardware_mapping": "regular",
  "logo_path": "/home/${PI_USER}/sign-controller/config/Logo-White.png",
  "customer_logo_path": "/home/${PI_USER}/sign-controller/config/customer_logo.png",
  "web_port": 8000,
  "auth": { "username": "admin", "password_hash": "${ADMIN_HASH}" }
}
JSON
  chown "${PI_USER}:${PI_USER}" "${SETTINGS_JSON}"
  chmod 0644 "${SETTINGS_JSON}"
fi

# placeholder logo
if [ ! -f "${CONF_DIR}/Logo-White.png" ]; then
  echo "==> Creating placeholder white logo"
  convert -size 256x128 xc:black -gravity center -pointsize 22 -fill white \
    -annotate 0 "LED Sign" "${CONF_DIR}/Logo-White.png"
fi
mkdir -p "${WEB_DIR}/static"
cp -f "${CONF_DIR}/Logo-White.png" "${WEB_DIR}/static/white-logo.png" || true

# -------- narrow privileged helper --------
# Install root-owned code rather than granting sudo to user-writable scripts.
sudo install -d -m 0755 /usr/local/libexec/flexclock
sudo install -o root -g root -m 0755 "${SCRIPTS_DIR}/web_network.py" /usr/local/libexec/flexclock/web_network.py
sudo install -o root -g root -m 0755 "${SCRIPTS_DIR}/web_hostname.py" /usr/local/libexec/flexclock/web_hostname.py
SUDOERS_FILE="/etc/sudoers.d/sign-controller-web"
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/flexclock/web_network.py scan, /usr/local/libexec/flexclock/web_network.py wifi, /usr/local/libexec/flexclock/web_network.py ethernet, /usr/local/libexec/flexclock/web_network.py wifi_address, /usr/local/libexec/flexclock/web_hostname.py ""\n' "$PI_USER" | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE"
# Configure only wlan0; preserve the installed manager and all Ethernet settings.
sudo bash "${SCRIPTS_DIR}/setup_wifi.sh"
"${PIP}" install -r "${WEB_DIR}/requirements.txt"

# -------- systemd: sign-web.service (Flask via waitress behind Nginx) --------
echo "==> systemd: sign-web.service"
sudo tee /etc/systemd/system/sign-web.service >/dev/null <<EOF
[Unit]
Description=LED Sign Web GUI
After=network-online.target
Wants=network-online.target

[Service]
User=${PI_USER}
SupplementaryGroups=systemd-journal
WorkingDirectory=${WEB_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${ROOT_DIR}/venv/bin/waitress-serve --listen=127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# -------- systemd: sign-ui.service (run UI / splash + mode selector) --------
echo "==> systemd: sign-ui.service"
if [ ! -f /etc/systemd/system/sign-ui.service ]; then
sudo tee /etc/systemd/system/sign-ui.service >/dev/null <<EOF
[Unit]
Description=LED Sign UI / Mode Runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${PI_USER}
WorkingDirectory=${ROOT_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=LEDSIGN_LYNX_PORT=${LYNX_PORT}
# Small delay to let the HAT / framebuffer settle
ExecStartPre=/bin/sleep 2
# Run the canonical entrypoint; it should launch splash.py then handle mode selection
ExecStart=${ROOT_DIR}/venv/bin/python ${ROOT_DIR}/boot/splash.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

fi

sudo systemctl daemon-reload
sudo systemctl enable --now sign-web
echo "Controller service preserved; start it only as part of supervised hardware commissioning."

# -------- nginx reverse proxy --------
echo "==> Nginx site"
sudo tee /etc/nginx/sites-available/sign-web >/dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 4m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /static/ {
        proxy_pass http://127.0.0.1:8000/static/;
        expires 7d;
    }
}
EOF
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/sign-web /etc/nginx/sites-enabled/sign-web
sudo systemctl restart nginx

# -------- perms & exec bits --------
echo "==> Permissions & exec bits"
# Ensure scripts are executable (especially run-ui.sh)
find "${SCRIPTS_DIR}" -type f -name "*.sh" -exec chmod +x {} \; || true
# Allow directly running python mode files if you choose to
find "${ROOT_DIR}/boot" -type f -name "*.py" -exec chmod +x {} \; || true
find "${ROOT_DIR}/modes" -type f -name "*.py" -exec chmod +x {} \; || true
sudo chown -R "${PI_USER}:${PI_USER}" "$ROOT_DIR"

# -------- info --------
echo "==> Install complete."
echo "Open:   http://<Pi-IP>/  (port 80 -> Flask via Nginx)"
echo "Login:  admin / admin (change via Web GUI)"
echo "UI:     systemctl status sign-ui   # splash + keyboard modes"
echo "Web:    systemctl status sign-web  # Flask (waitress)"
echo "Ports:  80 (web), ${LYNX_PORT} (Lynx TCP)"
