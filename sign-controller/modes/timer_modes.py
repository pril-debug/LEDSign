# /sign-controller/modes/timer_modes.py
import time, sys, termios, tty, select
from datetime import timedelta
from PIL import Image, ImageDraw
from .matrix_utils import load_font, push

DIR_UP, DIR_DOWN = 1, -1

def getch(timeout=0.05):
    dr,dw,de = select.select([sys.stdin], [], [], timeout)
    if not dr: return None
    return sys.stdin.read(1)

def fmt_hhmmss(total_seconds):
    if total_seconds < 0: total_seconds = 0
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def collect_hhmmss(matrix, prompt="Set Time (HHMMSS)"):
    digits = ""
    big = load_font(46)   # large time
    med = load_font(18)
    while True:
        img = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
        draw = ImageDraw.Draw(img)
        draw.text((8, 6), prompt, font=med, fill=(255,255,255))
        disp = digits.ljust(6, "_")
        # Show formatted live preview
        if len(digits) >= 1:
            hh = int(digits[0:2] or 0) if len(digits)>=2 else int(digits[0])
        if len(digits) == 6:
            hh = int(digits[0:2]); mm = int(digits[2:4]); ss = int(digits[4:6])
        else:
            hh = int(digits[0:2] or 0); mm = int(digits[2:4] or 0); ss = int(digits[4:6] or 0)
        preview = f"{hh:02d}:{mm:02d}:{ss:02d}"
        w = draw.textlength(preview, font=big)
        draw.text(((matrix.width - w)//2, 24), preview, font=big, fill=(255,255,255))
        push(matrix, img)

        c = getch(0.15)
        if not c: continue
        if c.isdigit() and len(digits) < 6:
            digits += c
        elif c in ('\x7f', '\b') and len(digits) > 0:
            digits = digits[:-1]
        elif c in ('\r','\n'):
            # Return seconds
            hh = int((digits[0:2] or "0").rjust(2,'0'))
            mm = int((digits[2:4] or "0").rjust(2,'0'))
            ss = int((digits[4:6] or "0").rjust(2,'0'))
            return hh*3600 + mm*60 + ss
        elif c == '\x1b':  # ESC cancel → back to menu
            return None

def draw_time(matrix, seconds):
    big = load_font(48)  # try to fill height on 64px
    img = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
    draw = ImageDraw.Draw(img)
    t = fmt_hhmmss(seconds)
    w = draw.textlength(t, font=big)
    draw.text(((matrix.width - w)//2, (matrix.height - big.size)//2), t, font=big, fill=(255,255,255))
    push(matrix, img)

def run_clock(matrix, direction=DIR_UP):
    # Numeric entry first
    start = collect_hhmmss(matrix, "Set Time (HHMMSS)")
    if start is None and direction == DIR_DOWN:
        return  # canceled
    if direction == DIR_UP and (start is None or start == 0):
        cur = 0
    else:
        cur = start or 0

    paused = False
    last = time.monotonic()
    draw_time(matrix, cur)

    # Controls: Enter = start/pause toggle, Space = pause, ESC = pause/exit, R = reset to 0
    running = False
    while True:
        # Input handling
        c = _get_key_nonblock()
        if c:
            if c in ('\r','\n'):  # Enter -> toggle run
                running = not running
            elif c in (' ','p','P'):
                running = False
            elif c in ('r','R'):
                cur = 0 if direction==DIR_UP else (start or 0)
                draw_time(matrix, cur)
            elif c == '\x1b':
                if running:
                    running = False
                    # first ESC pauses
                else:
                    # second ESC exits to menu
                    return

        now = time.monotonic()
        if running and now - last >= 1.0:
            step = int(now - last)
            last += step
            cur = cur + step if direction==DIR_UP else cur - step
            if direction == DIR_DOWN and cur <= 0:
                cur = 0
                running = False  # stop at 0, per spec
            draw_time(matrix, cur)
        else:
            time.sleep(0.02)

def _get_key_nonblock():
    import select, sys
    dr,_,_ = select.select([sys.stdin], [], [], 0)
    if not dr: return None
    return sys.stdin.read(1)
