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

# Where to fetch the default logo from (override with: LOGO_URL=... ./setup.sh)
LOGO_URL="${LOGO_URL:-https://raw.githubusercontent.com/pril-debug/LEDSign/main/Logo-White.png}"

echo "==> Updating APT and installing dependencies..."
sudo apt-get update
sudo apt-get install -y \
  git build-essential scons \
  python3 python3-dev python3-venv python3-pip python3-pillow \
  libfreetype6-dev libopenjp2-7 libjpeg-dev zlib1g-dev \
  pkg-config curl jq net-tools \
  fonts-dejavu-core imagemagick

echo "==> Creating project structure at ${ROOT_DIR} ..."
mkdir -p "$ROOT_DIR"/{modes,web/templates,web/static,boot,config}
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
# 'sudo' ensures the Python binding installs system-wide for python3 as well.
sudo make install PYTHON=python3 || true

echo "==> Setting up Python virtual environment ..."
python3 -m venv "${VENV_DIR}"
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip wheel
pip install flask waitress pillow netifaces
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
  "web_port": 8000
}
JSON

echo "==> Downloading Logo-White.png into config ..."
if curl -fsSL "${LOGO_URL}" -o "${CONFIG_DIR}/Logo-White.png"; then
  echo "      Downloaded logo from ${LOGO_URL}"
else
  echo "!!    Could not download logo from ${LOGO_URL}. Creating a placeholder instead..."
  convert -size 128x64 xc:black -gravity center \
    -pointsize 16 -fill white -annotate 0 "LED Sign" \
    "${CONFIG_DIR}/Logo-White.png" || true
fi

echo "==> Writing splash.py ..."
cat > "${BOOT_DIR}/splash.py" <<'PY'
#!/usr/bin/env python3
# Shows /config/Logo-White.png and overlays the Pi's IP at bottom-right
import os
from PIL import Image, ImageDraw, ImageFont
import json
from rgbmatrix import RGBMatrix, RGBMatrixOptions
import netifaces

CONF_PATH = os.path.expanduser("/home/pi/sign-controller/config/settings.json")

def load_conf():
    with open(CONF_PATH, "r") as f:
        return json.load(f)

def get_ip():
    # Try to find first non-loopback IPv4
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

    # Load logo and letterbox to panel size
    logo_path = conf.get("logo_path", "/home/pi/sign-controller/config/Logo-White.png")
    base = Image.new("RGB", (width, height), (0,0,0))
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGB")
        logo.thumbnail((width, height), Image.LANCZOS)
        # center
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
    tw, th = draw.textbbox((0,0), text, font=font)[2:]
    pad = 1
    # Draw a faint box for contrast
    draw.rectangle([width - tw - 2*pad, height - th - 2*pad, width-1, height-1], fill=(0,0,0))
    draw.text((width - tw - pad, height - th - pad), text, fill=(255,255,255), font=font)

    # Show on matrix
    matrix.SetImage(base)
    # Keep displayed until killed by mode switcher later
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
PY
chmod +x "${BOOT_DIR}/splash.py"

echo "==> Writing minimal Flask web (Phase 3 placeholder) ..."
cat > "${WEB_DIR}/app.py" <<'PY'
#!/usr/bin/env python3
from flask import Flask
import json, os

app = Flask(__name__)

@app.get("/")
def index():
    return "LED Track Sign Web is running. (Auth/UI coming next.)"

if __name__ == "__main__":
    port = 8000
    confp = "/home/pi/sign-controller/config/settings.json"
    if os.path.exists(confp):
        try:
            with open(confp) as f:
                port = int(json.load(f).get("web_port", 8000))
        except Exception:
            pass
    app.run(host="0.0.0.0", port=port)
PY

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

# LED library generally needs realtime-ish scheduling; this helps
# (if kernel supports). Fallback is harmless.
AmbientCapabilities=CAP_SYS_NICE

[Install]
WantedBy=multi-user.target
UNIT

echo "==> Creating systemd service: sign-web.service ..."
WEB_SERVICE="/etc/systemd/system/sign-web.service"
sudo tee "${WEB_SERVICE}" >/dev/null <<'UNIT'
[Unit]
Description=LED Track Sign - Flask Web
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/sign-controller/web
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/pi/sign-controller/venv/bin/waitress-serve --host=0.0.0.0 --port=8000 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

echo "==> Enabling and starting services ..."
sudo systemctl daemon-reload
sudo systemctl enable led-splash.service
sudo systemctl enable sign-web.service
sudo systemctl start led-splash.service
sudo systemctl start sign-web.service

echo "==> Fixing ownership to ${PI_USER}:${PI_USER} ..."
sudo chown -R "${PI_USER}:${PI_USER}" "${ROOT_DIR}"

echo "==> Done!"
echo
echo "Next steps:"
echo "  • If you have multiple panels, edit /home/pi/sign-controller/config/settings.json (e.g. led_chain: 2)."
echo "  • Upload your logo to GitHub at ${LOGO_URL} (or pass LOGO_URL env var) to control the default."
echo "  • Web test: curl http://<pi-ip>:8000/"
echo "  • To see splash logs: sudo journalctl -u led-splash -f"
