# /sign-controller/modes/lynx_receiver.py
"""
FinishLynx receiver mode for LED Track Sign
- Listens on UDP (default) or TCP for lines emitted by a FinishLynx .lss script
- Parses a small set of message types and updates the matrix display
- Pulls matrix options from /config/settings.json

Run standalone for quick tests:
  python3 modes/lynx_receiver.py --port 5010 --udp

Systemd unit suggestion:
  [Unit]
  Description=LED Sign - FinishLynx Receiver
  After=network-online.target
  Wants=network-online.target

  [Service]
  Type=simple
  WorkingDirectory=/home/PI_USER/sign-controller
  ExecStart=/home/PI_USER/sign-controller/venv/bin/python modes/lynx_receiver.py --port 5010 --udp
  Restart=always
  User=PI_USER
  Group=PI_USER

  [Install]
  WantedBy=multi-user.target

Message format (FlexSign minimalist protocol):
  # Clear screen and begin new event header/clock
  CL

  # Header (event line BIG, then large clock) — we only show Heat on SMALL if provided
  RH,"Boys 1600m","Event 12","Round Final","Heat 1"

  # Clock updates (we DO NOT compute time on Pi — this is just a display of Lynx's time)
  TM,2:03.7

  # Results ("Show Results" phase) — one line per athlete, last name only
  SR,1,"Riley","4:23.6"

  # Optional batch markers (nice for switching the layout into results mode)
  SRMODE,BEGIN
  SRMODE,END

Any unrecognized line is ignored gracefully.
"""
from __future__ import annotations
import argparse
import json
import queue
import select
import signal
import socket
import threading
import time
from pathlib import Path
from typing import List, Tuple

# rpi-rgb-led-matrix
try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
except Exception:  # allows dev on non-Pi
    RGBMatrix = None
    RGBMatrixOptions = object
    graphics = None

APP_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = APP_ROOT / "config" / "settings.json"
FONTS_DIR = APP_ROOT / "ledlib" / "fonts"

# --------------------------- parsing helpers ---------------------------

def parse_csv_line(line: str) -> List[str]:
    out, field, in_quotes = [], [], False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            if in_quotes and i + 1 < len(line) and line[i + 1] == '"':
                field.append('"'); i += 1
            else:
                in_quotes = not in_quotes
        elif c == ',' and not in_quotes:
            out.append(''.join(field).strip()); field = []
        else:
            field.append(c)
        i += 1
    out.append(''.join(field).strip())
    return out

# --------------------------- state ---------------------------

class State:
    def __init__(self):
        self.event = ""      # e.g., Boys 1600m
        self.heat  = ""      # e.g., Heat 1
        self.clock = "0:00"  # last clock string received (from Lynx)
        self.mode  = "HEADER" # HEADER | RESULTS
        self.results: List[Tuple[str,str]] = []  # [(place, mark)] where name is implied by last field
        self.results_names: List[str] = []       # [last_name]
        self.result_index = 0

    def clear(self):
        self.__init__()

# --------------------------- renderer ---------------------------

class Renderer:
    def __init__(self, settings: dict):
        if RGBMatrix is None:
            raise RuntimeError("rgbmatrix not available (run on Pi or install deps)")
        opts = RGBMatrixOptions()
        opts.rows = int(settings.get("led_rows", 64))
        opts.cols = int(settings.get("led_cols", 64))
        opts.chain_length = int(settings.get("led_chain", 2))
        opts.parallel = int(settings.get("led_parallel", 1))
        opts.pwm_bits = int(settings.get("led_pwm_bits", 11))
        opts.brightness = int(settings.get("led_brightness", 80))
        opts.gpio_slowdown = int(settings.get("led_gpio_slowdown", 2))
        opts.hardware_mapping = settings.get("led_hardware_mapping", "regular")
        self.matrix = RGBMatrix(options=opts)
        # Fonts
        self.font_title = graphics.Font(); self.font_title.LoadFont(str(FONTS_DIR / "10x20.bdf"))
        # Try large clock font; fall back if missing
        self.font_clock = graphics.Font()
        for cand in ["16x27.bdf", "14x26.bdf", "13x24.bdf", "10x20.bdf"]:
            try:
                self.font_clock.LoadFont(str(FONTS_DIR / cand))
                break
            except Exception:
                continue
        self.font_small = graphics.Font(); self.font_small.LoadFont(str(FONTS_DIR / "7x13.bdf"))
        # Colors
        self.white = graphics.Color(255,255,255)
        self.green = graphics.Color(0,255,0)

    def _center_text(self, canvas, font, text: str, y: int, color=None):
        if not text: return 0
        if color is None: color = self.white
        width = graphics.DrawText(canvas, font, 0, y, color, text)
        w = self.matrix.width
        if width < w:
            canvas.Clear()
            x = (w - width)//2
            graphics.DrawText(canvas, font, x, y, color, text)
            return width
        return width

    def render_header(self, st: State):
        canvas = self.matrix.CreateFrameCanvas()
        h = self.matrix.height
        # Event title on top
        self._center_text(canvas, self.font_title, st.event, 18)
        # Optional heat info small (right-aligned-ish)
        small = st.heat
        if small:
            graphics.DrawText(canvas, self.font_small, 2, h-2, self.white, small)
        # Big clock centered
        # place the baseline so digits are vertically centered
        self._center_text(canvas, self.font_clock, st.clock, 46)
        self.matrix.SwapOnVSync(canvas)

    def render_results(self, st: State):
        canvas = self.matrix.CreateFrameCanvas()
        w, h = self.matrix.width, self.matrix.height
        if not st.results:
            self.render_header(st); return
        i = st.result_index % len(st.results)
        place, mark = st.results[i]
        last = st.results_names[i]
        # Top line: "1 - Riley"
        top = f"{place} - {last}"
        self._center_text(canvas, self.font_title, top, 20)
        # Green thin bar
        graphics.DrawLine(canvas, 0, 28, w-1, 28, self.green)
        # Time under it, big
        self._center_text(canvas, self.font_clock, mark, 58)
        self.matrix.SwapOnVSync(canvas)

    def render(self, st: State):
        if st.mode == "RESULTS":
            self.render_results(st)
        else:
            self.render_header(st)

# --------------------------- receiver ---------------------------

def load_settings() -> dict:
    with open(CONF_PATH, 'r') as f:
        return json.load(f)

class Receiver(threading.Thread):
    def __init__(self, port: int, udp: bool):
        super().__init__(daemon=True)
        self.port = port
        self.udp = udp
        self.q: "queue.Queue[str]" = queue.Queue()
        self.stop_evt = threading.Event()

    def run(self):
        if self.udp:
            self._run_udp()
        else:
            self._run_tcp()

    def _run_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        sock.setblocking(False)
        while not self.stop_evt.is_set():
            r,_,_ = select.select([sock],[],[],0.25)
            if sock in r:
                data, _addr = sock.recvfrom(65535)
                for line in data.decode(errors='ignore').splitlines():
                    self.q.put(line.strip())

    def _run_tcp(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", self.port))
        server.listen(5)
        server.setblocking(False)
        conns = []
        while not self.stop_evt.is_set():
            r,_,_ = select.select([server] + conns, [], [], 0.25)
            if server in r:
                conn, _ = server.accept(); conn.setblocking(False); conns.append(conn)
            for c in list(conns):
                if c in r:
                    try:
                        data = c.recv(4096)
                        if not data:
                            conns.remove(c); c.close(); continue
                        for line in data.decode(errors='ignore').splitlines():
                            self.q.put(line.strip())
                    except Exception:
                        conns.remove(c); c.close()

# --------------------------- application logic ---------------------------

def apply_message(st: State, line: str):
    if not line: return
    parts = parse_csv_line(line)
    if not parts: return
    tag = parts[0].upper()

    if tag == 'CL':
        st.clear(); return

    if tag == 'RH':
        # RH, BigTitle, Event, Round, Heat
        st.event = parts[1] if len(parts) > 1 else ''
        heat = parts[4] if len(parts) > 4 else ''
        st.heat = heat if heat else ''
        st.clock = "0:00"  # show armed/cleared clock
        st.mode = 'HEADER'
        return

    if tag == 'TM':
        # TM,2:03.7  (display-only)
        st.clock = parts[1] if len(parts) > 1 else st.clock
        # stay in HEADER mode while timing
        st.mode = 'HEADER'
        return

    if tag == 'SRMODE':
        if len(parts) > 1 and parts[1].upper() == 'BEGIN':
            st.mode = 'RESULTS'; st.result_index = 0
        elif len(parts) > 1 and parts[1].upper() == 'END':
            st.mode = 'HEADER'
        return

    if tag == 'SR':
        # SR,place,last_name,mark
        place = parts[1] if len(parts) > 1 else ''
        last  = parts[2] if len(parts) > 2 else ''
        mark  = parts[3] if len(parts) > 3 else ''
        st.results.append((place, mark))
        st.results_names.append(last)
        # cap to last 16
        st.results = st.results[-16:]
        st.results_names = st.results_names[-16:]
        st.mode = 'RESULTS'
        return

    if tag == 'TX':
        # Optional free text overlay (kept compatible)
        st.event = parts[1] if len(parts) > 1 else st.event
        st.heat = ''
        st.mode = 'HEADER'
        return

    # ignore unknown


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=5010)
    ap.add_argument('--udp', action='store_true', help='listen via UDP (default)')
    ap.add_argument('--tcp', action='store_true', help='listen via TCP')
    ap.add_argument('--fps', type=float, default=20.0)
    ap.add_argument('--result-rotate-sec', type=float, default=2.0)
    args = ap.parse_args(argv)

    udp = not args.tcp

    settings = load_settings()
    renderer = Renderer(settings)

    st = State()

    recv = Receiver(args.port, udp=udp)
    recv.start()

    running = True
    def _sig(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    next_rotate = time.time() + args.result_rotate_sec
    frame_delay = 1.0 / max(5.0, args.fps)

    while running:
        try:
            while True:
                line = recv.q.get_nowait()
                apply_message(st, line)
        except queue.Empty:
            pass

        now = time.time()
        if st.mode == 'RESULTS' and st.results:
            if now >= next_rotate:
                st.result_index += 1
                next_rotate = now + args.result_rotate_sec
        else:
            next_rotate = now + args.result_rotate_sec

        renderer.render(st)
        time.sleep(frame_delay)

if __name__ == '__main__':
    main()

# ---------------------------------------------------------------------------
# /sign-controller/modes/FlexSign.lss  (FinishLynx script template)
# ---------------------------------------------------------------------------
# Copy this file to your FinishLynx computer and select it as your script Output.
# Configure Network-> Script Port to the LED Pi IP and UDP port (default 5010).
# Replace the $variables below with your Lynx script fields (names vary by version).
# If you share your existing `example`, `example2`, and `FlexSign.lss`, we can
# drop in the exact field tokens.

# ===== Event open / header =====
# Clear board and send event/heat
CL

# RH, "$event_name","Event $event","Round $round","Heat $heat"
# Only the first (event) and the heat are displayed on the sign per our layout.
RH,"$event_name","Event $event","Round $round","Heat $heat"

# Initialize the display clock to armed 0:00
TM,0:00

# ===== Race running — live clock =====
# Emit this periodically (e.g., 5–10 times per second is fine; even 2 Hz looks smooth)
# Use your Lynx field that outputs the CURRENT running time string for the race.
# Example placeholder: $running_time
TM,$running_time

# ===== Show Results phase =====
# Switch into results layout and send one SR line per finisher. Use LAST NAME only.
SRMODE,BEGIN

# SR,<place>,"<LAST>","<mark>"
SR,$place,"$last","$mark"

# Repeat SR lines for all places you want to rotate through on the sign, in order.
# When done:
SRMODE,END

# (Optional) To return to header/clock view for next race, you may send a new CL/RH/TM.
