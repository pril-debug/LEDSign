#!/usr/bin/env bash
set -euo pipefail

echo "==> LED Sign: starting install"

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

# -------- time & apt --------
echo "==> Syncing time / base system"
sudo timedatectl set-ntp true || true
sudo raspi-config nonint do_wifi_country US || true

echo "==> APT deps"
sudo apt-get update -y
sudo apt-get install -y \
  build-essential git python3 python3-venv python3-dev python3-pip \
  libjpeg-dev libpng-dev libfreetype6-dev pkg-config \
  libtiff5-dev libatlas-base-dev cython3 \
  nginx jq imagemagick network-manager

# -------- project layout --------
echo "==> Creating project directories"
mkdir -p "$ROOT_DIR" "$CONF_DIR" "$SCRIPTS_DIR"

# -------- rgb-matrix lib (cloned once) --------
if [ ! -d "${ROOT_DIR}/ledlib" ]; then
  echo "==> Cloning hzeller/rpi-rgb-led-matrix"
  git clone https://github.com/hzeller/rpi-rgb-led-matrix.git "${ROOT_DIR}/ledlib"
  make -C "${ROOT_DIR}/ledlib/lib"
  make -C "${ROOT_DIR}/ledlib/examples-api-use"
  make -C "${ROOT_DIR}/ledlib/bindings/python"
  ( cd "${ROOT_DIR}/ledlib/bindings/python" && sudo python3 setup.py install )
fi

# -------- python venv --------
if [ ! -d "${ROOT_DIR}/venv" ]; then
  echo "==> Python venv + packages"
  python3 -m venv "${ROOT_DIR}/venv"
  "${PIP}" install --upgrade pip wheel
  "${PIP}" install flask waitress pillow
fi

# -------- web app --------
if [ ! -d "$WEB_DIR" ]; then
  echo "==> Cloning web GUI (LEDSign_Site)"
  git clone https://github.com/pril-debug/LEDSign_Site.git "$WEB_DIR"
fi

# -------- configuration (settings.json + logos) --------
SETTINGS_JSON="${CONF_DIR}/settings.json"
if [ ! -f "$SETTINGS_JSON" ]; then
  echo "==> Writing default settings.json"

  # Choose the default admin password (env override supported)
  DEFAULT_PASS="${DEFAULT_ADMIN_PASSWORD:-admin}"

  # Generate a fresh password hash using Werkzeug in the project venv
  VENV_BIN="/home/${PI_USER}/sign-controller/venv/bin"
  if [ ! -x "${VENV_BIN}/python" ]; then
    echo "ERROR: venv python not found at ${VENV_BIN}/python" >&2
    exit 1
  fi
  ADMIN_HASH="$("${VENV_BIN}/python" - <<PY
from werkzeug.security import generate_password_hash
print(generate_password_hash("${DEFAULT_PASS}"))
PY
)"

  # Write the JSON with real user paths and the generated hash
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
  "auth": {
    "username": "admin",
    "password_hash": "${ADMIN_HASH}"
  }
}
JSON

  chown "${PI_USER}:${PI_USER}" "${SETTINGS_JSON}"
  chmod 0644 "${SETTINGS_JSON}"
fi

# placeholder white logo if missing
if [ ! -f "${CONF_DIR}/Logo-White.png" ]; then
  echo "==> Creating placeholder white logo"
  convert -size 256x128 xc:black -gravity center -pointsize 22 -fill white \
    -annotate 0 "LED Sign" "${CONF_DIR}/Logo-White.png"
fi

# ensure web/static logo path used by templates
mkdir -p "${WEB_DIR}/static"
cp -f "${CONF_DIR}/Logo-White.png" "${WEB_DIR}/static/white-logo.png"

# -------- network apply script (NetworkManager aware) --------
APPLY_SH="${SCRIPTS_DIR}/apply_network.sh"
echo "==> Installing network apply script"
cat > "$APPLY_SH" <<"BASH"
#!/usr/bin/env bash
set -euo pipefail
# Usage:
#   apply_network.sh eth0 dhcp
#   apply_network.sh eth0 static 192.168.0.56 24 192.168.0.1 [dns...]
IFACE="${1:-eth0}"
MODE="${2:-dhcp}"
IP="${3:-}"
CIDR="${4:-}"
GW="${5:-}"
DNS="${*:6}"

if systemctl is-active NetworkManager >/dev/null 2>&1; then
  CONN="Wired connection 1"
  if ! nmcli c show "$CONN" >/dev/null 2>&1; then
    nmcli c add type ethernet ifname "$IFACE" con-name "$CONN" >/dev/null
  fi

  if [ "$MODE" = "dhcp" ]; then
    nmcli c mod "$CONN" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "" ipv4.ignore-auto-dns no
  else
    CIDR_MASK="${IP}/${CIDR}"
    nmcli c mod "$CONN" ipv4.method manual ipv4.addresses "$CIDR_MASK" ipv4.gateway "$GW"
    if [ -n "$DNS" ]; then
      nmcli c mod "$CONN" ipv4.dns "$DNS" ipv4.ignore-auto-dns yes
    else
      nmcli c mod "$CONN" ipv4.dns "" ipv4.ignore-auto-dns no
    fi
  fi

  ip addr flush dev "$IFACE" || true
  nmcli c down "$CONN" || true
  nmcli c up "$CONN"
  exit 0
fi

# Fallback to dhcpcd if NM is not active
CONF="/etc/dhcpcd.conf"
TAG_BEGIN="# LEDSign ${IFACE} BEGIN"
TAG_END="# LEDSign ${IFACE} END"

sudo touch "$CONF"
sudo sed -i "/^${TAG_BEGIN}$/,/^${TAG_END}$/d" "$CONF"

{
  echo "$TAG_BEGIN"
  echo "interface ${IFACE}"
  if [ "$MODE" = "dhcp" ]; then
    echo "  # DHCP default"
  else
    echo "static ip_address=${IP}/${CIDR}"
    [ -n "$GW" ] && echo "static routers=${GW}"
    if [ -n "$DNS" ]; then
      echo "static domain_name_servers=${DNS// /,}"
    fi
  fi
  echo "$TAG_END"
} | sudo tee -a "$CONF" >/dev/null

sudo systemctl restart dhcpcd || true
BASH
chmod +x "$APPLY_SH"

# -------- sudoers for the web to run the script (no password) --------
echo "==> Sudoers rule"
SUDOERS_FILE="/etc/sudoers.d/sign-controller-web"
SUDO_LINE="${PI_USER} ALL=(root) NOPASSWD: /usr/bin/nmcli, /usr/sbin/ip, ${APPLY_SH}"
if [ ! -f "$SUDOERS_FILE" ] || ! sudo grep -qxF "$SUDO_LINE" "$SUDOERS_FILE"; then
  echo "$SUDO_LINE" | sudo tee "$SUDOERS_FILE" >/dev/null
  sudo chmod 440 "$SUDOERS_FILE"
fi

# -------- systemd service for the web --------
echo "==> systemd: sign-web.service"
sudo tee /etc/systemd/system/sign-web.service >/dev/null <<EOF
[Unit]
Description=LED Sign Web GUI
After=network-online.target
Wants=network-online.target

[Service]
User=${PI_USER}
WorkingDirectory=${WEB_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${ROOT_DIR}/venv/bin/waitress-serve --listen=127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sign-web

# -------- nginx reverse proxy --------
echo "==> Nginx site"
sudo tee /etc/nginx/sites-available/sign-web >/dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    # Static assets cache
    location /static/ {
        proxy_pass http://127.0.0.1:8000/static/;
        expires 7d;
    }
}
EOF
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/sign-web /etc/nginx/sites-enabled/sign-web
sudo systemctl restart nginx

# -------- ownerships --------
sudo chown -R "${PI_USER}:${PI_USER}" "$ROOT_DIR"

echo "==> Install complete."
echo "Open:   http://<Pi-IP>/"
echo "Login:  admin / admin"
echo "Note: Changing Ethernet via dashboard will immediately bounce the NIC."
