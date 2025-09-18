# /sign-controller/modes/menu.py
import time, sys, termios, tty, select
from PIL import Image, ImageDraw
from .matrix_utils import load_font, push

MENU = ["FinishLynx", "Clock Up", "Clock Down", "Lap Count", "Rounds"]

def getch(timeout=0.05):
    dr,dw,de = select.select([sys.stdin], [], [], timeout)
    if not dr: return None
    return sys.stdin.read(1)

def run_menu(matrix):
    # prepare raw terminal
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        idx = 0
        small = load_font(14)
        title = load_font(16)
        while True:
            img = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
            draw = ImageDraw.Draw(img)
            # Title
            draw.text((4, 2), "Select Mode", font=title, fill=(255,255,255))
            # Items
            y = 22
            for i, item in enumerate(MENU):
                prefix = "> " if i == idx else "  "
                draw.text((8, y), prefix + item, font=small, fill=(255,255,255))
                if i == idx:
                    w = draw.textlength(prefix + item, font=small)
                    draw.line((8, y+small.size+1, 8+w, y+small.size+1), fill=(255,255,255), width=1)
                y += small.size + 6

            push(matrix, img)

            c = getch(0.12)
            if not c:
                continue
            if c in ("\x1b",):  # ESC → could exit or ignore here
                # Ignore at menu
                pass
            elif c in ('w','W') or ord(c)==65:  # up (W or ANSI up)
                idx = (idx - 1) % len(MENU)
            elif c in ('s','S') or ord(c)==66:  # down (S or ANSI down)
                idx = (idx + 1) % len(MENU)
            elif c in ('\r','\n'):  # Enter
                sel = MENU[idx]
                if sel == "Clock Up":
                    from .timer_modes import run_clock, DIR_UP
                    run_clock(matrix, direction=DIR_UP)
                elif sel == "Clock Down":
                    from .timer_modes import run_clock, DIR_DOWN
                    run_clock(matrix, direction=DIR_DOWN)
                elif sel == "Lap Count":
                    from .lap_counter import run_lap_counter
                    run_lap_counter(matrix)
                elif sel == "FinishLynx":
                    # placeholder until we wire your Lynx receiver
                    from PIL import ImageFont
                    img = Image.new("RGB", (matrix.width, matrix.height), (0,0,0))
                    f = load_font(16)
                    ImageDraw.Draw(img).text((10, 20), "FinishLynx mode\n(coming next)", font=f, fill=(255,255,255))
                    push(matrix, img); time.sleep(1.2)
                elif sel == "Rounds":
                    from .rounds import run_rounds
                    run_rounds(matrix)
            # Consume ANSI escape sequences for arrows if needed
            elif c == '\x1b':
                # read rest of escape if present
                _ = sys.stdin.read(2) if select.select([sys.stdin], [], [], 0)[0] else None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
