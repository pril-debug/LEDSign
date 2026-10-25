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

def boot_frame(matrix, ip, mode="Count Up", message=None):
    bg = Image.new("RGB", (matrix.width, matrix.height), (0, 0, 0))
    draw = ImageDraw.Draw(bg)
    labels = ["> " + mode, ip or "No network"]
    # Prefer a single footer row; small panels stack the two corner labels.
    font = load_font(min(12, max(6, matrix.height // 6)))
    def bounds(text):
        return draw.textbbox((0, 0), text, font=font)
    while getattr(font, "size", 6) > 6 and max(bounds(t)[2] for t in labels) > matrix.width - 4:
        font = load_font(font.size - 1)
    boxes = [bounds(t) for t in labels]
    row_height = max(b[3]-b[1] for b in boxes) + 2
    stacked = sum(b[2]-b[0] for b in boxes) + 8 > matrix.width
    while stacked and 2 * row_height + 2 >= matrix.height and getattr(font, "size", 4) > 4:
        font = load_font(font.size - 1)
        boxes = [bounds(t) for t in labels]
        row_height = max(b[3]-b[1] for b in boxes) + 1
    footer_height = row_height * (2 if stacked else 1) + 2
    logo_area_height = max(1, matrix.height - footer_height)
    logo_path = find_logo()
    if logo_path:
        try:
            with Image.open(logo_path) as source:
                logo = scale_to_fit(source.convert("RGBA"), max(1, matrix.width - 4), logo_area_height)
            bg.paste(logo, ((matrix.width - logo.width)//2,
                           (logo_area_height - logo.height)//2), logo)
        except OSError:
            pass  # A damaged upload must not disable keyboard selection.
    for i, (label, box) in enumerate(zip(labels, boxes)):
        left, top, right, bottom = box
        x = 2 - left if i == 0 else matrix.width - 2 - right
        y = matrix.height - 2 - bottom - (row_height if stacked and i == 0 else 0)
        # Even the smallest supported panels retain the complete labels.
        tile = Image.new("RGB", (max(1, right-left), max(1, bottom-top)))
        ImageDraw.Draw(tile).text((-left, -top), label, font=font,
                                 fill="yellow" if i == 0 else "white")
        if tile.width > matrix.width - 4:
            tile = tile.resize((matrix.width - 4, tile.height))
            x = 2
        bg.paste(tile, (x, y + top))
    if message:
        # Error replaces only the logo area, leaving selection and address visible.
        draw.rectangle((0, 0, matrix.width-1, logo_area_height-1), fill="black")
        error = Image.new("RGB", (max(1, int(draw.textlength(message, font=font))), row_height))
        ImageDraw.Draw(error).text((0, -bounds(message)[1]), message, font=font, fill="red")
        error.thumbnail((matrix.width, logo_area_height))
        bg.paste(error, ((matrix.width-error.width)//2, (logo_area_height-error.height)//2))
    return bg


def show_ip(matrix):
    _, ip = get_primary_ip()
    push(matrix, boot_frame(matrix, ip))
    time.sleep(1.5)

def main():
    m = build_matrix()
    from modes.menu import run_menu
    run_menu(m)

if __name__ == "__main__":
    main()
