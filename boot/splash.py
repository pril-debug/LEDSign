# /sign-controller/boot/splash.py
#!/usr/bin/env python3
import time, os, sys
from pathlib import Path
from PIL import Image, ImageDraw

from modes.matrix_utils import build_matrix, read_conf, center_image_on, render_text, push, get_primary_ip, load_font

APP_ROOT = Path(__file__).resolve().parents[1]
CONF = read_conf()

def find_logo():
    p_custom = Path(CONF.get("customer_logo_path", ""))
    p_default = Path(CONF.get("logo_path", ""))
    for p in [p_custom, p_default]:
        if p.is_file():
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

def boot_frame(matrix, ip):
    bg = Image.new("RGB", (matrix.width, matrix.height), (0, 0, 0))
    draw = ImageDraw.Draw(bg)
    label = ip or "No network"
    font = load_font(10)
    while getattr(font, "size", 6) > 6 and draw.textbbox((0, 0), label, font=font)[2] > matrix.width - 4:
        font = load_font(font.size - 1)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    text_height = bottom - top
    logo_area_height = max(1, matrix.height - text_height - 6)
    logo_path = find_logo()
    if logo_path:
        with Image.open(logo_path) as source:
            logo = scale_to_fit(source.convert("RGBA"), matrix.width - 8, logo_area_height)
        bg.paste(logo, ((matrix.width - logo.width)//2,
                       (logo_area_height - logo.height)//2), logo)
    draw.text((matrix.width - 2 - right, matrix.height - 2 - bottom),
              label, font=font, fill="white")
    return bg


def show_ip(matrix):
    _, ip = get_primary_ip()
    push(matrix, boot_frame(matrix, ip))
    time.sleep(1.5)

def main():
    m = build_matrix()
    show_ip(m)

    # systemd has no keyboard terminal; keep the last frame displayed.
    if not sys.stdin.isatty():
        while True:
            time.sleep(2)
            _, ip = get_primary_ip()
            push(m, boot_frame(m, ip))
        return

    # Jump to menu
    from modes.menu import run_menu
    run_menu(m)

if __name__ == "__main__":
    main()
