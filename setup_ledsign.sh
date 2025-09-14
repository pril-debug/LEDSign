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
mkdir -p "$ROOT_DIR"
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
pip install --upgrade pip wheel flask waitress

echo "==> Cloning web GUI..."
if [ ! -d "$WEB_DIR" ]; then
  git clone https://github.com/pril-debug/LEDSign-Web.git "$WEB_DIR"
fi

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

echo "==> Done. Visit http://<Pi-IP>/ to see the login screen."
