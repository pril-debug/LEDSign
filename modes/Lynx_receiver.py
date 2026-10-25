"""FinishLynx scoreboard receiver: framed clocks and legacy CSV over UDP/TCP."""
from __future__ import annotations
import argparse
import os
import sys
import json
import queue
import re
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
sys.path.insert(0, str(APP_ROOT))
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
        self.clock = ""  # last clock string received (from Lynx)
        self.connected = False
        self.mode  = "HEADER" # HEADER | RESULTS
        self.results: List[Tuple[str,str]] = []  # [(place, mark)] where name is implied by last field
        self.results_names: List[str] = []       # [last_name]
        self.message = ''
        self.result_index = 0

    def clear(self):
        self.__init__()

# --------------------------- renderer ---------------------------

class Renderer:
    def __init__(self, settings: dict, matrix=None):
        if RGBMatrix is None and matrix is None:
            raise RuntimeError("rgbmatrix not available (run on Pi or install deps)")
        from modes.matrix_utils import build_matrix, load_font
        self.matrix = matrix if matrix is not None else build_matrix(settings)
        self.font_title = load_font(18)
        self.font_clock = load_font(27)
        self.font_small = load_font(12)

    def _frame(self, top, clock, heat='', results=False):
        from PIL import Image, ImageDraw
        w, h = self.matrix.width, self.matrix.height
        image = Image.new('RGB', (w, h))
        draw = ImageDraw.Draw(image)
        def centered(text, font, y):
            width = draw.textlength(text, font=font)
            draw.text(((w-width)//2, y), text, font=font, fill='white')
        centered(top, self.font_title, 0)
        if results:
            draw.line((0, h//2-4, w-1, h//2-4), fill='green')
        centered(clock, self.font_clock, max(0, h//2-5))
        if heat:
            draw.text((2, max(0, h-13)), heat, font=self.font_small, fill='white')
        self.matrix.SetImage(image)

    def render_header(self, st: State):
        self.matrix.SetImage(event_clock_frame(self.matrix.width, self.matrix.height, st.event, st.clock))

    def render_results(self, st: State):
        if not st.results:
            return self.render_header(st)
        from PIL import Image
        width, height = self.matrix.width, self.matrix.height
        image = Image.new('RGB', (width, height))
        i = st.result_index % len(st.results)
        place, mark = st.results[i]
        lines = [st.event, f'{place} {st.results_names[i]}'.strip(), mark]
        lines = [line for line in lines if line]
        for index, line in enumerate(lines):
            top, bottom = height*index//len(lines), height*(index+1)//len(lines)
            image.paste(clock_frame(width, bottom-top, line), (0, top))
        self.matrix.SetImage(image)

    def waiting_frame(self, connected):
        self.matrix.SetImage(waiting_image(self.matrix.width, self.matrix.height, connected))

    def render(self, st: State):
        if not st.connected and not st.event:
            self.waiting_frame(False)
            return
        if st.mode == 'MESSAGE':
            self.matrix.SetImage(clock_frame(self.matrix.width, self.matrix.height, st.message))
            return
        if st.clock and st.mode == 'HEADER':
            self.matrix.SetImage(event_clock_frame(self.matrix.width, self.matrix.height, st.event, st.clock))
            return
        if not st.clock and not st.event and not st.results:
            self.waiting_frame(True)
            return
        if st.mode == "RESULTS":
            self.render_results(st)
        else:
            self.render_header(st)

from functools import lru_cache


def stable_clock_frame(width, height, text):
    """Use plain tabular digits, a fixed baseline, and a centered clock field."""
    from PIL import Image, ImageDraw
    if not valid_clock(text):
        return clock_frame(width, height, text)
    numeric = text.split(' ')[0]
    hours = numeric.count(':') >= 2
    precision = len(numeric.partition('.')[2]) if '.' in numeric else 0
    # Reserve one minute digit even before the first minute; only a format
    # change (e.g. ten minutes) can change the scale, never a changing digit.
    leading = max(1, len(numeric.split(':')[0])) if ':' in numeric else 1
    font, template = clock_font(width, height, hours, precision,
                                text.upper().endswith(('AM', 'PM')), leading)
    image = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = font.getbbox(template)
    x = (width-draw.textlength(text, font=font))/2
    y = (height-(bottom-top))//2-top
    draw.text((x, y), text, font=font, fill='white')
    return image


@lru_cache(maxsize=64)
def clock_font(width, height, hours, precision, meridiem, leading=1):
    from PIL import ImageFont
    template = '8'*leading + (':88:88' if hours else ':88')
    if precision and not hours:
        template += '.' + '8'*precision
    if meridiem:
        template += ' PM'
    for size in range(height*2, 0, -1):
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', size)
        left, top, right, bottom = font.getbbox(template)
        if right-left <= width-2 and bottom-top <= height-2:
            return font, template
    return font, template


def event_clock_frame(width, height, event, clock):
    if not event:
        return stable_clock_frame(width, height, clock)
    from PIL import Image
    image = Image.new('RGB', (width, height))
    header_height = max(8, height//3)
    from PIL import ImageDraw, ImageOps
    event_image = clock_frame(width, header_height-1, event)
    event_image = ImageOps.colorize(event_image.convert('L'), black=(0, 0, 0), white=(0, 96, 255))
    image.paste(event_image, (0, 0))
    ImageDraw.Draw(image).rectangle((0, header_height-1, width-1, header_height), fill=(0, 96, 255))
    image.paste(stable_clock_frame(width, height-header_height-2, clock or '0:00.0'), (0, header_height+2))
    return image


def waiting_image(width, height, connected=False):
    from PIL import Image, ImageOps
    image = Image.new('RGB', (width, height))
    footer = max(8, height//5)
    logo_path = APP_ROOT / 'lynx-logo.png'
    if logo_path.exists():
        with Image.open(logo_path) as source:
            logo = ImageOps.contain(source.convert('RGBA'), (width, max(1, height-footer-2)))
            image.paste(logo, ((width-logo.width)//2, (height-footer-logo.height)//2), logo)
    else:
        image.paste(clock_frame(width, height-footer, 'FinishLynx'), (0, 0))
    label = clock_frame(width, footer, 'Connected' if connected else 'Waiting....')
    if connected:
        pixels = label.load()
        for y in range(label.height):
            for x in range(label.width):
                r, g, b = pixels[x, y]
                pixels[x, y] = (r//3, g, b//3)
    image.paste(label, (0, height-footer))
    return image


def clock_frame(width, height, text):
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(image)
    for size in range(height * 2, 0, -1):
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', size)
        left, top, right, bottom = font.getbbox(text)
        if right-left <= width-2 and bottom-top <= height-2:
            break
    draw.text(((width-right+left)//2-left, (height-bottom+top)//2-top),
              text, font=font, fill='white')
    return image


class Decoder:
    """LSS field selectors are instructions, not transmitted control bytes."""
    def __init__(self):
        self.buffer = b''

    def feed(self, data):
        self.buffer += data
        messages = []
        while self.buffer:
            self.buffer = self.buffer.lstrip(b'\x00\x03\x04\r\n')
            if not self.buffer:
                break
            ends = [index for index in (self.buffer.find(b'\x03'), self.buffer.find(b'\x04')) if index >= 0]
            end = min(ends) if ends else -1
            if end >= 0:
                frame, self.buffer = self.buffer[:end], self.buffer[end+1:]
                messages.extend(self.frame(frame))
                continue
            # Keep an entire framed message across UDP datagrams / TCP reads.
            if self.buffer.startswith((b'T\n', b'R\n', b'S\n', b'M\n', b'=0T', b'\x01', b'\x02')):
                break
            end = self.buffer.find(b'\n')
            if end < 0:
                break
            messages.append(self.buffer[:end].decode('ascii', errors='ignore').strip())
            self.buffer = self.buffer[end+1:]
        if len(self.buffer) > 65536:
            self.buffer = b''
        return messages

    def frame(self, frame):
        import csv
        import io
        text = frame.decode('cp1252', errors='replace').lstrip('\x00\x01\x02')
        text = re.sub(r'^=0(?=T)', '', text)
        tag, separator, body = text.partition('\n')
        def record(*fields):
            output = io.StringIO()
            csv.writer(output, lineterminator='').writerow(fields)
            return output.getvalue()
        if tag == 'T' and separator:
            # Styling after the clock is display-specific and does not alter time.
            match = re.match(r'\s*(\d{1,3}(?::\d{2}){0,2}(?:\.\d{1,3})?(?: ?[APap][Mm])?)(?=\s|[^0-9:.]|$)', body)
            if match and valid_clock(match[1]):
                return [record('TM', match[1])]
        if tag in ('R', 'S') and separator:
            body = body.replace('\x01', '').replace('\x04', '').replace('\x06', '')
            header, _, body = body.partition('\n')
            rows = [record('BOARD', tag, header.strip())]
            # Result has no final newline: the ten-character mark runs directly
            # into the next three-character place in the supplied script.
            while body.strip():
                place, sep, rest = body.partition('\n')
                if not sep:
                    break
                name, sep, rest = rest.partition('\n')
                if not sep:
                    break
                mark, body = rest[:10], rest[10:]
                rows.append(record('SR', place.strip(), name.strip(), mark.strip()))
                body = body.lstrip('\r\n')
            return rows
        if tag == 'M' and separator:
            return [record('MESSAGE', body.replace('\x01', '').strip())]
        return []


def valid_clock(value):
    return bool(re.fullmatch(r'\d{1,3}(?::\d{2}){0,2}(?:\.\d{1,3})?(?: ?[APap][Mm])?', value))


# --------------------------- receiver ---------------------------

def load_settings() -> dict:
    with open(CONF_PATH, 'r') as f:
        return json.load(f)

class Receiver(threading.Thread):
    def __init__(self, port: int, udp: bool):
        super().__init__(daemon=True)
        self.port = port
        self.udp = udp
        self.q: "queue.Queue[str]" = queue.Queue(maxsize=256)
        self.last_data = 0
        self.clients = 0
        self.port = port
        self.stop_evt = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.sockets = []

    @property
    def connected(self):
        return time.monotonic() - self.last_data < 5 if self.udp else self.clients > 0

    def receive(self, decoder, data):
        for message in decoder.feed(data):
            if message.startswith(('TM,', 'BOARD,', 'SR,', 'MESSAGE,')):
                self.last_data = time.monotonic()
            if self.q.full():
                self.q.get_nowait()
            self.q.put_nowait(message)

    def run(self):
        try:
            if self.udp:
                self._run_udp()
            else:
                self._run_tcp()
        except OSError as error:
            self.error = error
        finally:
            self.ready.set()
            for sock in self.sockets:
                sock.close()

    def _run_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sockets.append(sock)
        sock.bind(("0.0.0.0", self.port))
        self.port = sock.getsockname()[1]
        self.ready.set()
        sock.setblocking(False)
        decoders = {}
        while not self.stop_evt.is_set():
            r,_,_ = select.select([sock],[],[],0.25)
            if sock in r:
                data, _addr = sock.recvfrom(65535)
                if _addr not in decoders:
                    if len(decoders) >= 32:
                        decoders.pop(next(iter(decoders)))
                    decoders[_addr] = Decoder()
                self.receive(decoders[_addr], data)

    def _run_tcp(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sockets.append(server)
        server.bind(("0.0.0.0", self.port))
        self.port = server.getsockname()[1]
        server.listen(5)
        self.ready.set()
        server.setblocking(False)
        conns = []
        decoders = {}
        while not self.stop_evt.is_set():
            r,_,_ = select.select([server] + conns, [], [], 0.25)
            if server in r:
                conn, _ = server.accept(); conn.setblocking(False); conns.append(conn); self.sockets.append(conn)
                decoders[conn] = Decoder()
                self.clients = len(conns)
            for c in list(conns):
                if c in r:
                    try:
                        data = c.recv(4096)
                        if not data:
                            conns.remove(c); self.sockets.remove(c); decoders.pop(c); self.clients = len(conns); c.close(); continue
                        self.receive(decoders[c], data)
                    except Exception:
                        conns.remove(c); self.sockets.remove(c); decoders.pop(c); self.clients = len(conns); c.close()

# --------------------------- application logic ---------------------------

def apply_message(st: State, line: str):
    if not line: return
    parts = parse_csv_line(line)
    if not parts: return
    tag = parts[0].upper()

    if tag == 'BOARD' and len(parts) >= 3:
        if st.event != parts[2]:
            st.clock = ''
        st.event = parts[2]
        st.results = []
        st.results_names = []
        st.result_index = 0
        st.mode = 'HEADER'
        return

    if tag == 'MESSAGE' and len(parts) >= 2:
        st.message = parts[1]
        st.mode = 'MESSAGE'
        st.clock = ''
        st.event = ''
        return

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

    if tag == 'TM' and len(parts) > 1 and valid_clock(parts[1]):
        # TM,2:03.7  (display-only)
        value = parts[1]
        # Time-of-day packets use hours:minutes:seconds. Keep race precision.
        st.clock = re.sub(r'\.\d+', '', value) if value.count(':') == 2 else value
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


def main(argv=None, matrix=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get("LEDSIGN_LYNX_PORT", "2042")))
    ap.add_argument('--udp', action='store_true', help='listen via UDP (default)')
    ap.add_argument('--tcp', action='store_true', help='listen via TCP')
    ap.add_argument('--fps', type=float, default=20.0)
    ap.add_argument('--result-rotate-sec', type=float, default=2.0)
    args = ap.parse_args(argv)

    udp = not args.tcp

    settings = load_settings()
    renderer = Renderer(settings, matrix=matrix)

    st = State()

    recv = Receiver(args.port, udp=udp)
    recv.start()
    recv.ready.wait(timeout=2)
    if recv.error:
        raise recv.error

    running = True
    def _sig(*_):
        nonlocal running
        running = False
    if matrix is None:
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)

    next_rotate = time.time() + args.result_rotate_sec
    frame_delay = 1.0 / max(5.0, args.fps)

    last_report = 0
    try:
        while running:
            if recv.error:
                raise recv.error
            if matrix is not None:
                from .control import read_key
                if read_key(0) == '\x1b':
                    return
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

            st.connected = recv.connected
            if time.monotonic() - last_report >= 0.5:
                report = dict(updated=time.time(), running=True, connected=st.connected,
                              port=recv.port, protocol='UDP' if udp else 'TCP', clock=st.clock, mode=st.mode, event=st.event, results=st.results, message=st.message)
                status_path = APP_ROOT / 'config/finishlynx-status.json'
                temp = status_path.with_suffix('.tmp')
                temp.write_text(json.dumps(report))
                temp.replace(status_path)
                last_report = time.monotonic()
            renderer.render(st)
            if matrix is not None:
                from .control import ready, report
                report(mode="FinishLynx", phase="running", value=st.clock)
                ready()
            time.sleep(frame_delay)
    finally:
        recv.stop_evt.set()
        recv.join(timeout=1)
        status_path = APP_ROOT / 'config/finishlynx-status.json'
        status_path.write_text(json.dumps(dict(updated=time.time(), running=False, connected=False,
                                              port=recv.port, protocol='UDP' if udp else 'TCP', clock='')))

if __name__ == '__main__':
    main()
