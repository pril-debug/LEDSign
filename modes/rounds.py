import time
from PIL import ImageDraw
from .control import read_key, ready, report, initial_running
from .matrix_utils import push
from .timer_modes import collect_hhmmss, time_frame


def run_rounds(matrix):
    length = collect_hhmmss(matrix, 'Round time HH:MM:SS')
    if not length:
        return
    remaining = length
    running = initial_running()
    last = time.monotonic()
    previous = None
    while True:
        if previous != remaining:
            border = max(3, min(matrix.width, matrix.height) // 12) if remaining <= 3 else 0
            image = time_frame(matrix, remaining, padding=border+2 if border else None)
            if border:
                ImageDraw.Draw(image).rectangle((0, 0, matrix.width-1, matrix.height-1),
                                               outline='white' if remaining == 2 else 'red', width=border)
            push(matrix, image)
            previous = remaining
        report(phase='running' if running else 'ready', value=remaining)
        ready()
        key = _getch(0.05)
        if key == '\x1b':
            return
        if key in ('start', '\r', '\n'):
            running = True if key == 'start' else not running
            last = time.monotonic()
        elif key in ('pause', ' ', 'p', 'P'):
            running = False
        now = time.monotonic()
        if not running:
            last = now
        elif now - last >= 1:
            step = int(now - last)
            last += step
            remaining = ((remaining - step - 1) % length) + 1


def _getch(timeout=0.05):
    return read_key(timeout)
