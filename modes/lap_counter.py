# /sign-controller/modes/lap_counter.py
import time, sys, select
from PIL import Image, ImageDraw
from .matrix_utils import load_font, push

def run_lap_counter(matrix):
    n = 0
    big = load_font(56)
    def render():
        img = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
        draw = ImageDraw.Draw(img)
        text = str(n)
        w = draw.textlength(text, font=big)
        draw.text(((matrix.width - w)//2, (matrix.height - big.size)//2),
                  text, font=big, fill=(255,255,255))
        push(matrix, img)

    render()
    paused = False
    while True:
        c = _getch()
        if not c:
            time.sleep(0.03); continue
        if c in ('\r','\n','+'):
            n += 1; render()
        elif c in ('-','_'):
            n = max(0, n-1); render()
        elif c in ('0',):
            n = 0; render()
        elif c == '\x1b':  # ESC → back to menu
            return

def _getch(timeout=0.05):
    dr,_,_ = select.select([sys.stdin], [], [], timeout)
    if not dr: return None
    return sys.stdin.read(1)
