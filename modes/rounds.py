# /sign-controller/modes/rounds.py
import time, sys, select
from PIL import Image, ImageDraw
from .matrix_utils import load_font, push
from .timer_modes import collect_hhmmss, fmt_hhmmss

def run_rounds(matrix):
    # Input “MMSS” or “HHMMSS”; we’ll just reuse the HHMMSS collector.
    length = collect_hhmmss(matrix, "Round Len (HHMMSS)")
    if not length: return
    last_second_flash = False

    running = True
    while True:
        start = time.monotonic()
        remaining = length
        while remaining > 0:
            # Last second: flash border in red
            img = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
            draw = ImageDraw.Draw(img)
            big = load_font(48)
            t = fmt_hhmmss(remaining)
            w = draw.textlength(t, font=big)
            draw.text(((matrix.width - w)//2, (matrix.height - big.size)//2), t, font=big, fill=(255,255,255))

            if remaining <= 1:
                # red border
                draw.rectangle([0,0,matrix.width-1, matrix.height-1], outline=(255,0,0), width=2)

            push(matrix, img)

            # Keys: ESC pauses then ESC again exits to menu
            c = _getch(0.2)
            if c == '\x1b':
                # pause
                if not _pause_screen(matrix):  # False means user chose to exit
                    return

            # tick
            now = time.monotonic()
            sleep_left = 1.0 - (now - start)
            if sleep_left > 0: time.sleep(sleep_left)
            start += 1.0
            remaining -= 1

        # Immediately restart, to ensure N * length == exact wall time N*length
        # (no extra pauses). The loop continues.
        # Optional: brief invert could be added, but you asked for immediate restart.

def _pause_screen(matrix):
    # Returns True to continue, False to exit to menu
    small = load_font(16)
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
    draw = ImageDraw.Draw(img)
    draw.text((10, 20), "Paused\nESC again to menu\nEnter to continue", font=small, fill=(255,255,255))
    push(matrix, img)
    # Wait for key
    while True:
        c = _getch(0.1)
        if c in ('\r','\n'):
            return True
        if c == '\x1b':
            return False

def _getch(timeout=0.05):
    dr,_,_ = select.select([sys.stdin], [], [], timeout)
    if not dr: return None
    return sys.stdin.read(1)
