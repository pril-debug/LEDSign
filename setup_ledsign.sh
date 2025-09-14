#!/usr/bin/env bash
set -euo pipefail

# ---- Config ----
PI_USER="${PI_USER:-pi}"
HOME_DIR="/home/${PI_USER}"
ROOT_DIR="${HOME_DIR}/sign-controller"
LEDLIB_DIR="${ROOT_DIR}/ledlib"
VENV_DIR="${ROOT_DIR}/venv"
WEB_DIR="${ROOT_DIR}/web"
BOOT_DIR="${ROOT_DIR}/boot"
MODES_DIR="${ROOT_DIR}/modes"
CONFIG_DIR="${ROOT_DIR}/config"
SCRIPTS_DIR="${ROOT_DIR}/scripts"

# Web repo (edit if your GitHub username/org differs)
WEB_REPO="${WEB_REPO:-https://github.com/pril-debug/LEDSign_Site.git}"
WEB_REF="${WEB_REF:-main}"

# Default logo source (override: LOGO_URL=... ./setup.sh)
LOGO_URL="${LOGO_URL:-https://raw.githubusercontent.com/pril-debug/LEDSign/main/Logo-White.png}"

echo "==> Updating APT and installing dependencies..."
sudo apt-get update
sudo apt-get install -y \
  git build-essential scons \
  python3 python3-dev python3-venv python3-pip python3-pillow \
  libfreetype6-dev libopenjp2-7 libjpeg-dev zlib1g-dev \
  pkg-config curl jq net-tools \
  fonts-dejavu-core imagemagick \
  nginx wireless-tools wpasupplicant

echo "==> Creating project structure at ${ROOT_DIR} ..."
mkdir -p "$ROOT_DIR"/{modes,web,boot,config,scripts}
cd "$ROOT_DIR"

echo "==> Cloning hzeller/rpi-rgb-led-matrix into ${LEDLIB_DIR} ..."
if [ ! -d "${LEDLIB_DIR}" ]; then
  git clone --depth=1 https://github.com/hzeller/rpi-rgb-led-matrix.git "${LEDLIB_DIR}"
else
  echo "      Already exists. Pulling latest..."
  (cd "${LEDLIB_DIR}" && git pull --ff-only || true)
fi

echo "==> Building C++ core (examples) ..."
make -C "${LEDLIB_DIR}" -j"$(nproc)" || true

echo "==> Building + installing Python bindings ..."
cd "${LEDLIB_DIR}/bindings/python"
make -j"$(nproc)" || true
sudo make install PYTHON=python3 || true

echo "==> Setting up Python virtual environment ..."
python3 -m venv "${VENV_DIR}"
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel
deactivate

echo "==> Creating default settings.json ..."
cat > "${CONFIG_DIR}/settings.json" <<'JSON'
{
  "led_rows": 64,
  "led_cols": 64,
  "led_chain": 2,
  "led_parallel": 1,
  "led_pwm_bits": 11,
  "led_brightness": 80,
  "led_gpio_slowdown": 2,
  "led_hardware_mapping": "regular",
  "logo_path": "/home/pi/sign-controller/config/Logo-White.png",
  "customer_logo_path": "/home/pi/sign-controller/config/customer_logo.png",
  "web_port": 8000,
  "auth": {
    "username": "admin",
    "password_hash": ""
  }
}
JSON

# Default admin/admin password hash (Werkzeug pbkdf2:sha256)
DEFAULT_PWHASH="pbkdf2:sha256:600000$DkZ0t4KzWwW2s4Lz$3bc0d6d6a2f8db6f9b8d7f3fa5a7c5d3f9f2e1e6e0d8c2a30f5df0a39ad9fd70"
jq --arg ph "$DEFAULT_PWHASH" '.auth.password_hash=$ph' "${CONFIG_DIR}/settings.json" > "${CONFIG_DIR}/settings.json.tmp"
mv "${CONFIG_DIR}/settings.json.tmp" "${CONFIG_DIR}/settings.json"

echo "==> Downloading White logo into config ..."
if curl -fsSL "${LOGO_URL}" -o "${CONFIG_DIR}/Logo-White.png"; then
  echo "      Downloaded logo from ${LOGO_URL}"
else
  echo "!!    Could not download logo. Creating a placeholder..."
  convert -size 256x128 xc:black -gravity center \
    -pointsize 20 -fill white -annotate 0 "LED Sign" \
    "${CONFIG_DIR}/Logo-White.png" || true
fi
rm -f "${CONFIG_DIR}/customer_logo.png" || true

echo "==> Writing splash.py ..."
cat > "${BOOT_DIR}/splash.py" <<'PY'
#!/usr/bin/env python3
import os, json, time
from PIL import Image, ImageDraw, ImageFont
from rgbmatrix import RGBMatrix, RGBMatrixOptions
import netifaces

CONF_PATH = os.path.expanduser("/home/pi/sign-controller/config/settings.json")

def load_conf():
    with open(CONF_PATH, "r") as f:
        return json.load(f)

def get_ip():
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
        for a in addrs:
            ip = a.get('addr')
            if ip and not ip.startswith('127.'):
                return ip
    return "no-ip"

def build_matrix(conf):
    opts = RGBMatrixOptions()
    opts.rows = conf.get("led_rows", 64)
    opts.cols = conf.get("led_cols", 64)
    opts.chain_length = conf.get("led_chain", 1)
    opts.parallel = conf.get("led_parallel", 1)
    opts.pwm_bits = conf.get("led_pwm_bits", 11)
    opts.brightness = conf.get("led_brightness", 80)
    opts.hardware_mapping = conf.get("led_hardware_mapping", "regular")
    opts.gpio_slowdown = conf.get("led_gpio_slowdown", 2)
    return RGBMatrix(options=opts)

def main():
    conf = load_conf()
    matrix = build_matrix(conf)
    width = conf.get("led_cols", 64) * conf.get("led_chain", 1)
    height = conf.get("led_rows", 64) * conf.get("led_parallel", 1)

    logo_path = conf.get("logo_path", "/home/pi/sign-controller/config/Logo-White.png")
    base = Image.new("RGB", (width, height), (0,0,0))
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGB")
        logo.thumbnail((width, height), Image.LANCZOS)
        x = (width - logo.width)//2
        y = (height - logo.height)//2
        base.paste(logo, (x,y))

    draw = ImageDraw.Draw(base)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except Exception:
        font = ImageFont.load_default()

    ip = get_ip()
    text = f"{ip}"
    bbox = draw.textbbox((0,0), text, font=font)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    pad = 1
    draw.rectangle([width - tw - 2*pad, height - th - 2*pad, width-1, height-1], fill=(0,0,0))
    draw.text((width - tw - pad, height - th - pad), text, fill=(255,255,255), font=font)

    matrix.SetImage(base)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
PY
chmod +x "${BOOT_DIR}/splash.py"

echo "==> Writing helper scripts ..."
# Apply Ethernet config via dhcpcd
cat > "${SCRIPTS_DIR}/apply_network.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
# stdin JSON: { "mode":"dhcp"|"static", "ip":"", "mask":"", "gw":"" }
CONF="/etc/dhcpcd.conf"
TMP="$(mktemp)"
read -r JSON
MODE=$(echo "$JSON" | jq -r '.mode')
IP=$(echo "$JSON" | jq -r '.ip // ""')
MASK=$(echo "$JSON" | jq -r '.mask // ""')
GW=$(echo "$JSON" | jq -r '.gw // ""')

# Remove our old managed block
if [ -f "$CONF" ]; then
  awk '
    BEGIN{skip=0}
    /# BEGIN sign-controller/ {skip=1; next}
    /# END sign-controller/ {skip=0; next}
    skip==0 {print}
  ' "$CONF" > "$TMP"
else
  : > "$TMP"
fi

{
  echo "# BEGIN sign-controller"
  echo "# Managed by sign-controller"
  echo "interface eth0"
  if [ "$MODE" = "static" ]; then
    echo "static ip_address=${IP}/${MASK}"
    echo "static routers=${GW}"
    echo "static domain_name_servers=1.1.1.1 8.8.8.8"
  fi
  echo "# END sign-controller"
} >> "$TMP"

install -m 644 "$TMP" "$CONF"
rm -f "$TMP"

systemctl restart dhcpcd
echo "OK"
SH
chmod +x "${SCRIPTS_DIR}/apply_network.sh"

# Wi-Fi scan
cat > "${SCRIPTS_DIR}/wifi_scan.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
IFACE="${1:-wlan0}"
ip link set "$IFACE" up || true
RAW=$(iwlist "$IFACE" scanning 2>/dev/null || true)
echo "["; first=1
echo "$RAW" | awk -F: '
/Cell/ {ssid=""; level="";}
/ESSID/ {gsub(/"/,"",$2); ssid=$2;}
/Signal level/ {split($2,a,"="); level=a[2]; gsub(/ dBm/,"",level); print ssid"\t"level;}
' | while IFS=$'\t' read -r SSID LEVEL; do
  [ -z "$SSID" ] && continue
  if [ $first -eq 0 ]; then echo ","; fi
  first=0
  printf '{"ssid":%q,"signal":%q}' "$SSID" "$LEVEL"
done
echo "]"
SH
chmod +x "${SCRIPTS_DIR}/wifi_scan.sh"

# Wi-Fi apply (PSK only)
cat > "${SCRIPTS_DIR}/apply_wifi.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
# stdin JSON: { "ssid":"...", "psk":"..." }
IFACE="wlan0"
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
read -r JSON
SSID=$(echo "$JSON" | jq -r '.ssid')
PSK=$(echo "$JSON" | jq -r '.psk')

mkdir -p "$(dirname "$WPA_CONF")"

TMP="$(mktemp)"
{
  echo 'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev'
  echo 'update_config=1'
  echo 'country=US'
  echo ''
  echo '# BEGIN sign-controller'
  echo 'network={'
  printf '    ssid="%s"\n' "$SSID"
  printf '    psk="%s"\n' "$PSK"
  echo '    key_mgmt=WPA-PSK'
  echo '}'
  echo '# END sign-controller'
} > "$TMP"

install -m 600 "$TMP" "$WPA_CONF"
rm -f "$TMP"

ip link set "$IFACE" up || true
wpa_cli -i "$IFACE" reconfigure || systemctl restart wpa_supplicant || true
dhclient "$IFACE" || true
echo "OK"
SH
chmod +x "${SCRIPTS_DIR}/apply_wifi.sh"

echo "==> Allowing passwordless sudo for the helper scripts ..."
SUDOERS="/etc/sudoers.d/sign-controller"
sudo bash -c "cat > '$SUDOERS' <<SUD
${PI_USER} ALL=(root) NOPASSWD: ${ROOT_DIR}/scripts/apply_network.sh
${PI_USER} ALL=(root) NOPASSWD: ${ROOT_DIR}/scripts/wifi_scan.sh
${PI_USER} ALL=(root) NOPASSWD: ${ROOT_DIR}/scripts/apply_wifi.sh
SUD"
sudo chmod 440 "$SUDOERS"

echo "==> Fetching Web GUI from ${WEB_REPO} (${WEB_REF}) ..."
if [ -d "${WEB_DIR}/.git" ]; then
  echo "      Existing checkout detected. Updating..."
  git -C "${WEB_DIR}" fetch --all --tags --prune
  git -C "${WEB_DIR}" checkout "${WEB_REF}"
  git -C "${WEB_DIR}" pull --ff-only || true
else
  rm -rf "${WEB_DIR}"
  git clone "${WEB_REPO}" "${WEB_DIR}"
  git -C "${WEB_DIR}" checkout "${WEB_REF}"
fi

echo "==> Installing web requirements into venv ..."
source "${VENV_DIR}/bin/activate"
if [ -f "${WEB_DIR}/requirements.txt" ]; then
  pip install -r "${WEB_DIR}/requirements.txt"
else
  pip install flask waitress werkzeug pillow
fi
deactivate

# Make sure the landing logo exists in the site's static dir
mkdir -p "${WEB_DIR}/static"
cp -f "${CONFIG_DIR}/Logo-White.png" "${WEB_DIR}/static/white-logo.png"

echo "==> Creating systemd service: led-splash.service ..."
LED_SERVICE="/etc/systemd/system/led-splash.service"
sudo tee "${LED_SERVICE}" >/dev/null <<'UNIT'
[Unit]
Description=LED Track Sign Splash (logo + IP at boot)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/sign-controller/boot
ExecStart=/home/pi/sign-controller/venv/bin/python /home/pi/sign-controller/boot/splash.py
Restart=on-failure
Nice=-5
AmbientCapabilities=CAP_SYS_NICE

[Install]
WantedBy=multi-user.target
UNIT

echo "==> Creating systemd service: sign-web.service ..."
WEB_SERVICE="/etc/systemd/system/sign-web.service"
sudo tee "${WEB_SERVICE}" >/dev/null <<'UNIT'
[Unit]
Description=LED Track Sign - Flask (waitress)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/sign-controller/web
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/pi/sign-controller/venv/bin/waitress-serve --host=127.0.0.1 --port=8000 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

echo "==> Configuring Nginx as reverse proxy on :80 ..."
NGINX_SITE="/etc/nginx/sites-available/sign-controller"
sudo tee "${NGINX_SITE}" >/dev/null <<'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX

sudo ln -sf "${NGINX_SITE}" /etc/nginx/sites-enabled/default
sudo nginx -t

echo "==> Enable + start services ..."
sudo systemctl daemon-reload
sudo systemctl enable led-splash.service
sudo systemctl enable sign-web.service
sudo systemctl restart nginx
sudo systemctl start led-splash.service
sudo systemctl start sign-web.service

echo "==> Fixing ownership to ${PI_USER}:${PI_USER} ..."
sudo chown -R "${PI_USER}:${PI_USER}" "${ROOT_DIR}"

echo "==> Done!"
echo
echo "Open:   http://<pi-ip>/"
echo "Login:  admin / admin   (change it in Dashboard)"
echo
echo "Logs:"
echo "  • Web (waitress):   sudo journalctl -u sign-web -f"
echo "  • Nginx:            sudo journalctl -u nginx -f"
echo "  • Splash:           sudo journalctl -u led-splash -f"
