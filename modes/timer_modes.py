# /sign-controller/modes/timer_modes.py
import time, sys, termios, tty, select
from datetime import timedelta
from PIL import Image, ImageDraw
from .control import read_key, ready, report, initial_running
from .matrix_utils import load_font, push

DIR_UP, DIR_DOWN = 1, -1

def getch(timeout=0.05):
    return read_key(timeout)

def fmt_hhmmss(total_seconds):
    if total_seconds < 0: total_seconds = 0
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def clock_text(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    if minutes:
        return f"{minutes}:{seconds:02d}"
    return str(seconds)


def fitted_font(text, width, height):
    # Measure the visible glyph bounds, including descenders and bearings.
    for size in range(max(1, height * 2), 0, -1):
        font = load_font(size)
        left, top, right, bottom = font.getbbox(text)
        if right - left <= width and bottom - top <= height:
            return font
    return load_font(1)


def entry_frame(matrix, digits, prompt):
    image = Image.new("RGB", (matrix.width, matrix.height))
    draw = ImageDraw.Draw(image)
    padding = max(1, min(matrix.width, matrix.height) // 32)
    header_height = max(5, matrix.height // 5)
    font = fitted_font(prompt, matrix.width - 2*padding, header_height)
    left, top, right, bottom = font.getbbox(prompt)
    draw.text(((matrix.width-right+left)//2-left, padding-top), prompt, font=font, fill='white')
    area_top = padding + header_height + padding
    area_height = max(1, matrix.height-area_top-padding)
    # Fixed-width cells keep the active position steady as digits are typed.
    font = fitted_font('00:00:00', matrix.width-2*padding, area_height)
    while font.size > 1:
        cell = max(draw.textlength(c, font=font) for c in '0123456789_')
        colon = draw.textlength(':', font=font)
        if 6*cell+2*colon <= matrix.width-2*padding:
            break
        font = load_font(font.size-1)
    cell = max(draw.textlength(c, font=font) for c in '0123456789_')
    colon = draw.textlength(':', font=font)
    x = (matrix.width-6*cell-2*colon)/2
    bounds = font.getbbox('0123456789_')
    y = area_top+(area_height-(bounds[3]-bounds[1]))//2-bounds[1]
    for index in range(6):
        if index in (2, 4):
            draw.text((x, y), ':', font=font, fill='white')
            x += colon
        character = digits[index] if index < len(digits) else '_'
        active = index == min(len(digits), 5)
        color = 'red' if active else 'white' if index < len(digits) else (80, 80, 80)
        draw.text((x+(cell-draw.textlength(character, font=font))/2, y), character,
                  font=font, fill=color)
        x += cell
    return image


def collect_hhmmss(matrix, prompt="Set time HH:MM:SS"):
    digits = ""
    while True:
        push(matrix, entry_frame(matrix, digits, prompt))
        report(phase="entry")
        ready(stage="entry")
        c = getch(0.15)
        if not c:
            continue
        if c in '0123456789' and len(digits) < 6:
            # Minutes and seconds each range from 00 to 59.
            if len(digits) in (2, 4) and int(c) > 5:
                continue
            digits += c
        elif c in ('\x7f', '\b'):
            digits = digits[:-1]
        elif c in ('\r', '\n'):
            padded = digits.ljust(6, '0')
            return int(padded[:2])*3600 + int(padded[2:4])*60 + int(padded[4:])
        elif c == '\x1b':
            return None


def number_frame(matrix, text, padding=None):
    image = Image.new("RGB", (matrix.width, matrix.height))
    draw = ImageDraw.Draw(image)
    if padding is None:
        padding = max(1, min(matrix.width, matrix.height)//32)
    font = fitted_font(text, matrix.width-2*padding, matrix.height-2*padding)
    left, top, right, bottom = font.getbbox(text)
    glyphs = Image.new("RGB", (max(1, right-left), max(1, bottom-top)))
    ImageDraw.Draw(glyphs).text((-left, -top), text, font=font, fill='white')
    visible = glyphs.getbbox()
    if visible:
        glyphs = glyphs.crop(visible)
        image.paste(glyphs, ((matrix.width-glyphs.width)//2,
                            (matrix.height-glyphs.height)//2))
    return image


def time_frame(matrix, seconds, padding=None):
    return number_frame(matrix, clock_text(seconds), padding)


def draw_time(matrix, seconds):
    push(matrix, time_frame(matrix, seconds))

def run_clock(matrix, direction=DIR_UP):
    # Numeric entry first
    start = collect_hhmmss(matrix, "Set time HH:MM:SS")
    if start is None:
        return  # canceled
    if direction == DIR_UP and (start is None or start == 0):
        cur = 0
    else:
        cur = start or 0

    running = initial_running() and (direction == DIR_UP or cur > 0)
    has_started = running
    last = time.monotonic()
    draw_time(matrix, cur)
    report(phase="running" if running else "ready", value=cur)
    ready()

    # Controls: Enter = start/pause toggle, Space = pause, ESC = pause/exit, R = reset to 0
    while True:
        # Input handling
        c = _get_key_nonblock()
        if c:
            if c in ('\r','\n'):  # Enter -> toggle run
                running = not running
                has_started = True
                last = time.monotonic()
            elif c == 'start':
                running = True
                has_started = True
                last = time.monotonic()
            elif c in (' ','p','P', 'pause'):
                running = False
            elif c in ('r','R'):
                cur = 0 if direction==DIR_UP else (start or 0)
                last = time.monotonic()
                draw_time(matrix, cur)
            elif c == '\x1b':
                if running:
                    running = False
                    # first ESC pauses
                else:
                    # second ESC exits to menu
                    return

        report(phase="running" if running else "paused" if has_started else "ready", value=cur)
        if c in ("start", "pause"):
            ready()
        now = time.monotonic()
        if not running:
            last = now
        if running and now - last >= 1.0:
            step = int(now - last)
            last += step
            cur = cur + step if direction==DIR_UP else cur - step
            if direction == DIR_DOWN and cur <= 0:
                cur = 0
                running = False  # stop at 0, per spec
            draw_time(matrix, cur)
            report(phase="running" if running else "paused" if has_started else "ready", value=cur)
        else:
            time.sleep(0.02)

def _get_key_nonblock():
    return read_key(0)
