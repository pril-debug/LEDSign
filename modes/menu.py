"""Boot selection and dispatch; operating modes retain their own controls."""
import logging
import os
import select
import sys
import termios
import time
import tty

from .matrix_utils import get_primary_ip, push
from . import control

MENU = ['Count Up', 'Count Down', 'Lap Counter', 'Round Counter', 'FinishLynx']


def getch(timeout=0.12):
    key = control.read_key(timeout)
    if key == '\x1b':
        tail = ''
        for _ in range(2):
            if select.select([sys.stdin], [], [], 0.05)[0]:
                tail += os.read(sys.stdin.fileno(), 1).decode(errors='ignore')
            else:
                break
        return {'[A': 'up', 'OA': 'up', '[B': 'down', 'OB': 'down'}.get(tail)
    return key


def launch_mode(matrix, index):
    control.report(mode=MENU[index], phase="entry")
    if index in (0, 1):
        from .timer_modes import run_clock, DIR_UP, DIR_DOWN
        run_clock(matrix, direction=DIR_UP if index == 0 else DIR_DOWN)
    elif index == 2:
        from .lap_counter import run_lap_counter
        run_lap_counter(matrix)
    elif index == 3:
        from .rounds import run_rounds
        run_rounds(matrix)
    elif index == 4:
        from .Lynx_receiver import main
        main([], matrix=matrix)
    else:
        raise NotImplementedError


def run_menu(matrix):
    from boot.splash import boot_frame
    control.start()
    fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    old = termios.tcgetattr(fd) if fd is not None else None
    if fd is not None:
        tty.setcbreak(fd)
    index = 0
    message = None
    try:
        while True:
            control.report(mode="Menu", phase="menu")
            _, ip = get_primary_ip()
            push(matrix, boot_frame(matrix, ip, MENU[index], message))
            try:
                key = getch()
                if key in ('up', '-', 'w', 'W'):
                    index = (index - 1) % len(MENU)
                    message = None
                elif key in ('down', '+', 's', 'S'):
                    index = (index + 1) % len(MENU)
                    message = None
                elif key in ('\r', '\n'):
                    launch_mode(matrix, index)
                    message = None
            except control.SelectMode as selection:
                while True:
                    index = control.apply_selection(selection)
                    try:
                        launch_mode(matrix, index)
                        message = None
                        break
                    except control.SelectMode as next_selection:
                        selection = next_selection
                    except Exception:
                        control.ready(error='Unable to start mode.')
                        logging.exception('Selected mode failed')
                        message = 'Unable to start mode'
                        break
            except Exception:
                logging.exception('Selected mode failed')
                message = 'Unable to start mode'
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
