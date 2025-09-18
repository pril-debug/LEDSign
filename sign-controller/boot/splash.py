# /sign-controller/boot/splash.py
#!/usr/bin/env python3
import time, os
from pathlib import Path
from PIL import Image

from modes.matrix_utils import build_matrix, read_conf, center_image_on, render_text, push, get_primary_ip, load_font

APP_ROOT = Path(__file__).resolve().parents[1]
CONF = read_conf()

def find_logo():
    p_custom = Path(CONF.get("customer_logo_path", ""))
    p_default = Path(CONF.get("logo_path", ""))
    for p in [p_custom, p_default]:
        if p and Path(p).exists():
            return Path(p)
    return None

def scale_to_fit(img, max_w, max_h):
    img = img.copy()
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    return img

def show_splash(matrix, seconds=3):
    bg = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
    logo_path = find_logo()
    if logo_path:
        logo = Image.open(logo_path).convert("RGBA")
        # Fit logo with padding
        padded = scale_to_fit(logo, matrix.width-8, matrix.height-8)
        center_image_on(bg, padded)
    push(matrix, bg)
    time.sleep(seconds)

def show_ip(matrix):
    bg = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
    iface, ip = get_primary_ip()
    small = load_font(16)
    if ip:
        render_text(bg, f"{iface}: {ip}", small, xy=("center","center"))
    else:
        render_text(bg, "no network", small, xy=("center","center"))
    push(matrix, bg)
    time.sleep(1.5)

def main():
    m = build_matrix()
    show_splash(m, 3)
    show_ip(m)

    # Jump to menu
    from modes.menu import run_menu
    run_menu(m)

if __name__ == "__main__":
    main()
