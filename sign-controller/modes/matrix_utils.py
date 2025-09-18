# /sign-controller/modes/matrix_utils.py
import json, socket, fcntl, struct, os, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from rgbmatrix import RGBMatrix, RGBMatrixOptions

APP_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = APP_ROOT / "config" / "settings.json"

# ---- Fonts (use built-in if TTF missing) ----
def load_font(size=12, fallback=True):
    # Try a decent TTF if present; else built-in 6x10 scaled by Pillow
    ttfs = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
    ]
    for t in ttfs:
        if os.path.exists(t):
            return ImageFont.truetype(t, size)
    # fallback bitmap-ish
    return ImageFont.load_default() if fallback else None

def read_conf():
    with open(CONF_PATH, "r") as f:
        return json.load(f)

def build_matrix():
    conf = read_conf()
    # Your new physical layout: 3 panels chained horizontally, 2 in parallel
    # Each panel is 64x32 → final = 192x64
    options = RGBMatrixOptions()
    options.rows       = 32                   # per-panel
    options.cols       = 64                   # per-panel
    options.chain_length = 3
    options.parallel   = 2
    options.pwm_bits   = conf.get("led_pwm_bits", 11)
    options.brightness = conf.get("led_brightness", 100)  # crank as needed outdoors
    options.gpio_slowdown = conf.get("led_gpio_slowdown", 2)
    options.hardware_mapping = conf.get("led_hardware_mapping", "regular")
    # Outdoor panels are bright; leave pwm_lsb_nanoseconds default, tune later
    return RGBMatrix(options=options)

def canvas_image(matrix):
    # Return a PIL image sized to the matrix
    width  = matrix.width
    height = matrix.height
    return Image.new("RGB", (width, height)), ImageDraw.Draw(Image.new("RGB", (width, height)))

def get_primary_ip():
    """
    Prefer an active interface/address (IPv4). If none, return None.
    We test common ifaces in priority order; you asked for a single primary.
    """
    candidates = ["eth0", "wlan0"]
    for iface in candidates:
        ip = iface_ipv4(iface)
        if ip:
            return iface, ip
    return None, None

def iface_ipv4(ifname):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            fcntl.ioctl(
                s.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack('256s', ifname.encode('utf-8')[:15])
            )[20:24]
        )
    except OSError:
        return None

def center_image_on(img_bg, img_fg):
    bg_w, bg_h = img_bg.size
    fg_w, fg_h = img_fg.size
    x = (bg_w - fg_w)//2
    y = (bg_h - fg_h)//2
    img_bg.paste(img_fg, (x, y), img_fg if img_fg.mode == "RGBA" else None)

def render_text(img, text, font, fill=(255,255,255), xy=("center","center")):
    draw = ImageDraw.Draw(img)
    w, h = draw.textbbox((0,0), text, font=font)[2:]
    if xy[0] == "center": x = (img.width - w)//2
    else: x = xy[0]
    if xy[1] == "center": y = (img.height - h)//2
    else: y = xy[1]
    draw.text((x, y), text, font=font, fill=fill)
    return (x, y, w, h)

def push(matrix, img):
    matrix.SetImage(img.convert("RGB"))
