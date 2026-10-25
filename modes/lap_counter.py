# /sign-controller/modes/lap_counter.py
import time, sys, select
from .control import read_key, ready, report
from .matrix_utils import push
from .timer_modes import number_frame

def run_lap_counter(matrix):
    n = 0
    def render():
        push(matrix, number_frame(matrix, str(n)))
        report(phase="ready", value=n)

    render()
    ready()
    paused = False
    while True:
        c = _getch()
        if not c:
            time.sleep(0.03); continue
        if c in ('\r','\n','+', 'lap_plus'):
            n = min(999999, n+1); render()
        elif c in ('-','_', 'lap_minus'):
            n = max(0, n-1); render()
        elif c in ('0',):
            n = 0; render()
        elif c.startswith('lap_set:'):
            n = int(c.split(':')[1]); render()
        elif c == '\x1b':  # ESC → back to menu
            return
        if c.startswith("lap_"):
            ready()

def _getch(timeout=0.05):
    return read_key(timeout)
