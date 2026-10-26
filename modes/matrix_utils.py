# /sign-controller/modes/matrix_utils.py
import json, socket, fcntl, struct, os, time, pwd
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

class MappedMatrix:
    """Expose one logical side to every mode; pack all panels on one output."""
    def __init__(self, conf):
        from display_layout import saved_layout, validate_layout
        active = APP_ROOT / 'config/display-active.json'
        state = json.loads(active.read_text()) if active.exists() else {}
        if state.get('test_until') and time.time() >= state['test_until'] and state.get('restore'):
            state['layout'] = state['restore']
        self.layout = validate_layout(state['layout']) if state else saved_layout(conf)
        self.test_until = state.get('test_until', 0)
        self.width = self.layout['width'] * self.layout['columns']
        self.height = self.layout['height'] * self.layout['rows']
        options = RGBMatrixOptions()
        options.rows = self.layout['height']
        options.cols = self.layout['width']
        options.chain_length = len(self.layout['panels'])
        options.parallel = 1
        options.multiplexing = conf.get('led_multiplexing', 0)
        options.pwm_bits = conf.get('led_pwm_bits', 11)
        options.brightness = conf.get('led_brightness', 80)
        options.gpio_slowdown = conf.get('led_gpio_slowdown', 2)
        options.hardware_mapping = conf.get('led_hardware_mapping', 'regular')
        # Initialize GPIO as root, then run rendering as the repository owner.
        # The driver's default daemon user cannot traverse a private home folder.
        owner = (pwd.getpwnam(os.environ["LEDSIGN_DEVICE_USER"]) if os.environ.get("LEDSIGN_DEVICE_USER")
                 else pwd.getpwuid(APP_ROOT.stat().st_uid))
        options.drop_privileges = False
        self.matrix = RGBMatrix(options=options)
        if os.getuid() == 0 and owner.pw_uid != 0:
            os.setgroups([])
            os.setgid(owner.pw_gid)
            os.setuid(owner.pw_uid)

    def SetImage(self, image):
        from display_layout import pack_image
        if image.size != (self.width, self.height):
            raise ValueError('Frame does not match the configured logical display size')
        self.matrix.SetImage(pack_image(image, self.layout, time.time() < self.test_until))
        from modes.control import capture_frame
        capture_frame(image)


def build_matrix(conf=None):
    return MappedMatrix(conf if conf is not None else read_conf())

def canvas_image(matrix):
    # Return a PIL image sized to the matrix
    width  = matrix.width
    height = matrix.height
    image = Image.new("RGB", (width, height))
    return image, ImageDraw.Draw(image)

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


def hotspot_status():
    """Return ready hotspot details for the menu footer only."""
    try:
        state = json.loads(Path('/run/flexclock-hotspot.json').read_text())
        if (isinstance(state, dict) and state.get('mode') == 'hotspot' and isinstance(state.get('ssid'), str)
                and state['ssid'].startswith('FlexClock-')):
            return dict(ssid=state['ssid'], url='http://192.168.4.1')
    except (OSError, ValueError, TypeError):
        pass
    return None
