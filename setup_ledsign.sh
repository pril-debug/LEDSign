#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "[setup] ERROR at line $LINENO"; exit 1' ERR

echo "==> LED Sign: starting install"

# -------- knobs --------
# Default to dhcpcd-only (matches web UI). Override with:
#   USE_NM=true bash setup_ledsign.sh
USE_NM="${USE_NM:-false}"

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
sudo raspi-config nonint do_wifi_country US || true

echo "==> APT deps"
sudo apt-get update -y
PKGS=(
  build-essential git curl jq
  python3 python3-venv python3-dev python3-pip
  libjpeg-dev libpng-dev libfreetype6-dev pkg-config
  libtiff5-dev libatlas-base-dev cython3
  nginx imagemagick
  openresolv
  iw wpasupplicant rfkill
)
if [ "$USE_NM" = "true" ]; then
  PKGS+=(network-manager)
else
  PKGS+=(dhcpcd5)
fi
sudo apt-get install -y "${PKGS[@]}"

# -------- choose ONE network manager --------
if [ "$USE_NM" = "true" ]; then
  echo "==> Enabling NetworkManager, disabling dhcpcd"
  sudo systemctl disable --now dhcpcd || true
  sudo systemctl enable  --now NetworkManager
  sudo mkdir -p /etc/NetworkManager/conf.d
  sudo tee /etc/NetworkManager/conf.d/10-keyfile.conf >/dev/null <<'INI'
[main]
plugins=keyfile
INI
  sudo systemctl restart NetworkManager
else
  echo "==> Enabling dhcpcd, disabling NetworkManager & networkd"
  sudo systemctl disable --now NetworkManager || true
  sudo systemctl disable --now systemd-networkd systemd-networkd-wait-online || true
  sudo systemctl unmask dhcpcd || true
  sudo systemctl enable  --now dhcpcd
  if [ -f /etc/network/interfaces ] && grep -qE '^\s*iface\s+eth0\s' /etc/network/interfaces; then
    echo "==> NOTE: /etc/network/interfaces contains eth0 config; dhcpcd expects it empty."
  fi
fi

# -------- project layout --------
echo "==> Creating project directories"
mkdir -p "$ROOT_DIR" "$CONF_DIR" "$SCRIPTS_DIR"

# -------- clone/update app repo (LEDSign) --------
mkdir -p "$(dirname "$ROOT_DIR")"
if [ -d "${ROOT_DIR}/.git" ]; then
  echo "==> Updating app repo"
  ( cd "$ROOT_DIR" && git fetch origin "$APP_REPO_BRANCH" && git reset --hard "origin/${APP_REPO_BRANCH}" )
elif [ ! -d "$ROOT_DIR" ] || [ -z "$(ls -A "$ROOT_DIR" 2>/dev/null)" ]; then
  echo "==> Cloning app repo: ${APP_REPO_URL} (${APP_REPO_BRANCH})"
  git clone --branch "$APP_REPO_BRANCH" --depth 1 "$APP_REPO_URL" "$ROOT_DIR"
else
  echo "==> Converting existing directory to repo checkout"
  ( cd "$ROOT_DIR" \
    && git init \
    && (git remote add origin "$APP_REPO_URL" 2>/dev/null || git remote set-url origin "$APP_REPO_URL") \
    && git fetch origin "$APP_REPO_BRANCH" \
    && git reset --hard "origin/${APP_REPO_BRANCH}" )
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
  rm -rf "${WEB_DIR}" || true
  git clone --branch "$WEB_REPO_BRANCH" --depth 1 "$WEB_REPO_URL" "$WEB_DIR"
else
  echo "==> Updating web GUI"
  ( cd "$WEB_DIR" && git fetch origin "$WEB_REPO_BRANCH" && git reset --hard "origin/${WEB_REPO_BRANCH}" )
fi

# -------- configuration (settings.json + logos) --------
SETTINGS_JSON="${CONF_DIR}/settings.json"
if [ ! -f "$SETTINGS_JSON" ]; then
  echo "==> Writing default settings.json"
  DEFAULT_PASS="${DEFAULT_ADMIN_PASSWORD:-admin}"
  VENV_BIN="${ROOT_DIR}/venv/bin"
  ADMIN_HASH="$("${VENV_BIN}/python" - <<PY
from werkzeug.security import generate_password_hash
print(generate_password_hash("${DEFAULT_PASS}"))
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

# -------- Wi-Fi scripts (scan + apply) --------
echo "==> Installing Wi-Fi scripts"
sudo tee "$WIFI_SCAN" >/dev/null <<"BASH"
#!/usr/bin/env bash
set -Eeuo pipefail
IFACE="${1:-wlan0}"
LANG=C
ip link show "$IFACE" >/dev/null 2>&1 || { echo "[]" ; exit 0; }
ip link set "$IFACE" up || true
sleep 0.4
SCAN_OUT="$(iw dev "$IFACE" scan 2>/dev/null || true)"
echo "$SCAN_OUT" | awk '
/^BSS / {mac=$2}
/freq:/ {freq=$2}
/signal:/ {sig=$2}
/SSID:/ { s=$0; sub(/^[[:space:]]*SSID:[[:space:]]*/,"",s); ssid=s }
/RSN:/ {secure=1}
/WPA:/ {secure=1}
/^$/ {
  if (ssid != "") {
    printf "SSID:%s|SIG:%s|FREQ:%s|SEC:%d\n", ssid, sig, freq, secure ? 1 : 0
  }
  ssid=""; sig=""; freq=""; secure=0
}
END{
  if (ssid != "") {
    printf "SSID:%s|SIG:%s|FREQ:%s|SEC:%d\n", ssid, sig, freq, secure ? 1 : 0
  }
}' | awk 'NF' | jq -R -s '
  split("\n")[:-1]
  | map(
      capture("SSID:(?<ssid>.*)\\|SIG:(?<sig>[^|]*)\\|FREQ:(?<freq>[^|]*)\\|SEC:(?<sec>\\d)")
      | .ssid = (.ssid | gsub("\\s+$"; "")) 
      | .signal = (.sig | tonumber? // -100)
      | .freq   = (.freq | tonumber? // 0)
      | .secure = (.sec == "1")
      | .chan   = (
          if .freq >= 2412 and .freq <= 2484 then ((.freq - 2407)/5 | floor)
          elif .freq >= 5160 and .freq <= 5885 then ((.freq - 5000)/5 | floor)
          else 0 end
        )
      | {ssid, signal, freq, chan, secure}
    )'
BASH
sudo chmod +x "$WIFI_SCAN"

sudo tee "$WIFI_APPLY" >/dev/null <<"BASH"
#!/usr/bin/env bash
set -Eeuo pipefail
PAYLOAD="$(cat)"
SSID="$(printf '%s' "$PAYLOAD" | jq -r '.ssid // ""')"
PSK="$(printf  '%s' "$PAYLOAD" | jq -r '.psk  // ""')"
IFACE="${1:-wlan0}"
CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
[ -n "$SSID" ] || { echo "Missing SSID" >&2; exit 1; }
rfkill unblock wifi || true
ip link set "$IFACE" up || true
install -d -m 0755 /etc/wpa_supplicant
bash -c "cat > '$CONF'" <<EOF
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={
    ssid="$SSID"
$( if [ -n "$PSK" ]; then
     cat <<EOPS
    psk="$PSK"
    key_mgmt=WPA-PSK
EOPS
   else
     echo '    key_mgmt=NONE'
   fi )
}
EOF
chmod 600 "$CONF"
systemctl stop NetworkManager 2>/dev/null || true
if systemctl list-unit-files | grep -q '^wpa_supplicant.service'; then
  systemctl restart wpa_supplicant || true
fi
systemctl restart dhcpcd || true
sleep 2
ip -4 -o addr show dev "$IFACE" | awk '{print $4}' || true
BASH
sudo chmod +x "$WIFI_APPLY"

# -------- network apply script (fixed DNS + logging) --------
APPLY_SH="${SCRIPTS_DIR}/apply_network.sh"
echo "==> Installing network apply script"
sudo tee "$APPLY_SH" >/dev/null <<"BASH"
#!/usr/bin/env bash
set -Eeuo pipefail
LOG=/var/log/ledsign-apply.log
log(){ printf '%(%Y-%m-%d %H:%M:%S)T [apply] %s\n' -1 "$*" | tee -a "$LOG" >&2; }
IFACE="${1:-}"; MODE="${2:-dhcp}"; IP="${3:-}"; CIDR="${4:-}"; GW="${5:-}"
DNS="${*:6}"
detect_iface(){ ip route get 1.1.1.1 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++){if($i=="dev"){print $(i+1);exit}}}'; }
flush_addr(){ ip addr flush dev "$1" || true; ip -6 addr flush dev "$1" || true; }
[ -n "$IFACE" ] || IFACE="$(detect_iface || true)"
IFACE="${IFACE:-eth0}"
MODE="${MODE,,}"; [ "$MODE" = "static" ] || MODE="dhcp"
log "requested: IFACE=$IFACE MODE=$MODE IP=$IP/$CIDR GW=$GW DNS='${DNS}'"
if systemctl is-active --quiet NetworkManager; then
  PROFILE="LEDSign-${IFACE}"
  nmcli -t -f NAME,DEVICE c show 2>/dev/null | grep ":${IFACE}$" | cut -d: -f1 | while read -r NAME; do
    [ "$NAME" != "$PROFILE" ] && nmcli -g NAME c show "$NAME" >/dev/null 2>&1 && nmcli c delete "$NAME" || true
  done
  nmcli -g NAME c show "$PROFILE" >/dev/null 2>&1 || nmcli c add type ethernet ifname "$IFACE" con-name "$PROFILE" || true
  nmcli c mod "$PROFILE" connection.interface-name "$IFACE" connection.autoconnect yes ipv6.method ignore
  if [ "$MODE" = "dhcp" ]; then
    nmcli c mod "$PROFILE" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "" ipv4.ignore-auto-dns no
  else
    nmcli c mod "$PROFILE" ipv4.method manual ipv4.addresses "${IP}/${CIDR}" ipv4.gateway "$GW"
    if [ -n "$DNS" ]; then nmcli c mod "$PROFILE" ipv4.dns "$DNS" ipv4.ignore-auto-dns yes; else nmcli c mod "$PROFILE" ipv4.dns "" ipv4.ignore-auto-dns no; fi
  fi
  flush_addr "$IFACE"; nmcli c down "$PROFILE" >/dev/null 2>&1 || true; nmcli c up "$PROFILE"; log "applied via NetworkManager"; exit 0
fi
CONF="/etc/dhcpcd.conf"
TAG_BEGIN="# LEDSign ${IFACE} BEGIN"; TAG_END="# LEDSign ${IFACE} END"
sudo touch "$CONF"
sudo sed -i "/^${TAG_BEGIN}$/,/^${TAG_END}$/d" "$CONF"
{
  echo "$TAG_BEGIN"
  echo "interface ${IFACE}"
  if [ "$MODE" = "dhcp" ]; then
    echo "  # Use DHCP"
  else
    echo "nohook dhcp"
    echo "static ip_address=${IP}/${CIDR}"
    [ -n "$GW" ]  && echo "static routers=${GW}"
    if [ -n "$DNS" ]; then
      echo "static domain_name_servers=${DNS}"
    fi
  fi
  echo "$TAG_END"
} | sudo tee -a "$CONF" >/dev/null
flush_addr "$IFACE"
sudo systemctl restart dhcpcd || true
sleep 1
NEWIP="$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' || true)"
log "dhcpcd applied; current ${IFACE} addr: ${NEWIP:-unknown}"
exit 0
BASH
sudo chmod +x "$APPLY_SH"
sudo touch /var/log/ledsign-apply.log && sudo chown root:"${PI_USER}" /var/log/ledsign-apply.log && sudo chmod 664 /var/log/ledsign-apply.log

# -------- sudoers --------
echo "==> Sudoers rule"
SUDOERS_FILE="/etc/sudoers.d/sign-controller-web"
SUDO_LINE="${PI_USER} ALL=(root) NOPASSWD: ${APPLY_SH}, ${WIFI_SCAN}, ${WIFI_APPLY}, /usr/bin/nmcli, /usr/sbin/iw, /usr/sbin/rfkill, /usr/sbin/ip, /bin/systemctl"
if [ ! -f "$SUDOERS_FILE" ] || ! sudo grep -qxF "$SUDO_LINE" "$SUDOERS_FILE"; then
  echo "$SUDO_LINE" | sudo tee "$SUDOERS_FILE" >/dev/null
  sudo chmod 440 "$SUDOERS_FILE"
  sudo visudo -cf "$SUDOERS_FILE" >/dev/null
fi

# -------- systemd: sign-web.service (Flask via waitress behind Nginx) --------
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

# -------- systemd: sign-ui.service (run UI / splash + mode selector) --------
echo "==> systemd: sign-ui.service"
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
ExecStart=/bin/bash -lc '${ROOT_DIR}/scripts/run-ui.sh'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sign-web
sudo systemctl enable --now sign-ui

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
