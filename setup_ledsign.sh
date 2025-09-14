#!/usr/bin/env bash
set -euo pipefail

# ---- Config ----
if [ -z "${PI_USER:-}" ]; then
  if id -u pi >/dev/null 2>&1; then
    PI_USER=pi
  else
    PI_USER="$(whoami)"
  fi
fi
HOME_DIR="$(getent passwd "$PI_USER" | cut -d: -f6)"
ROOT_DIR="${HOME_DIR}/sign-controller"
WEB_DIR="${ROOT_DIR}/web"
CONFIG_DIR="${ROOT_DIR}/config"

echo "==> Syncing system time..."
sudo timedatectl set-ntp true || true
sudo raspi-config nonint do_wifi_country US || true

echo "==> Updating APT and installing dependencies..."
sudo apt-get update -y
sudo apt-get install -y \
  build-essential git python3 python3-venv python3-dev python3-pip \
  libjpeg-dev libpng-dev libfreetype6-dev pkg-config \
  libtiff5-dev libatlas-base-dev \
  nginx cython3 jq imagemagick wireless-tools wpasupplicant

echo "==> Creating project structure..."
mkdir -p "$ROOT_DIR" "$CONFIG_DIR"
cd "$ROOT_DIR"

echo "==> Cloning hzeller/rpi-rgb-led-matrix..."
[ ! -d ledlib ] && git clone https://github.com/hzeller/rpi-rgb-led-matrix.git ledlib
cd ledlib
make -C lib
make -C examples-api-use
make -C bindings/python

echo "==> Installing Python bindings..."
cd bindings/python
sudo python3 setup.py install

echo "==> Setting up Python virtual environment..."
cd "$ROOT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel flask waitress pillow
deactivate

echo "==> Creating default settings.json ..."
cat > "${CONFIG_DIR}/settings.json" <<JSON
{
  "led_rows": 64,
  "led_cols": 64,
  "led_chain": 2,
  "led_parallel": 1,
  "led_pwm_bits": 11,
  "led_brightness": 80,
  "led_gpio_slowdown": 2,
  "led_hardware_mapping": "regular",
  "logo_path": "${CONFIG_DIR}/Logo-White.png",
  "customer_logo_path": "${CONFIG_DIR}/customer_logo.png",
  "web_port": 8000,
  "auth": {
    "username": "admin",
    "password_hash": "pbkdf2:sha256:600000\$DkZ0t4KzWwW2s4Lz\$3bc0d6d6a2f8db6f9b8d7f3fa5a7c5d3f9f2e1e6e0d8c2a30f5df0a39ad9fd70"
  }
}
JSON

echo "==> Creating placeholder logo if missing..."
if [ ! -f "${CONFIG_DIR}/Logo-White.png" ]; then
  convert -size 256x128 xc:black -gravity center \
    -pointsize 20 -fill white -annotate 0 "LED Sign" \
    "${CONFIG_DIR}/Logo-White.png"
fi

echo "==> Cloning web GUI..."
if [ ! -d "$WEB_DIR" ]; then
  git clone https://github.com/pril-debug/LEDSign_Site.git "$WEB_DIR"
fi

echo "==> Copying logo into web static dir..."
mkdir -p "${WEB_DIR}/static"
cp -f "${CONFIG_DIR}/Logo-White.png" "${WEB_DIR}/static/white-logo.png"

echo "==> Setting up systemd service..."
sudo tee /etc/systemd/system/sign-web.service >/dev/null <<EOF
[Unit]
Description=LED Sign Web GUI
After=network.target

[Service]
User=$PI_USER
WorkingDirectory=$WEB_DIR
ExecStart=$ROOT_DIR/venv/bin/waitress-serve --listen=127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sign-web

echo "==> Configuring nginx reverse proxy..."
sudo tee /etc/nginx/sites-available/sign-web >/dev/null <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/sign-web /etc/nginx/sites-enabled/sign-web
sudo systemctl restart nginx

echo "==> Done."
echo "Visit:  http://<Pi-IP>/"
echo "Login:  admin / admin"
