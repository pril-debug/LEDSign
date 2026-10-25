"""Local dashboard input, consumed by the controller's existing input loops."""
import base64
import io
import json
import os
from pathlib import Path
import queue
import select
import socket
import sys
import threading
import time

SOCKET_PATH = Path(__file__).resolve().parents[1] / 'config/controller.sock'
MODES = ('Count Up', 'Count Down', 'Lap Counter', 'Round Counter', 'FinishLynx')
_commands = queue.Queue(maxsize=1)
_keys = queue.Queue()
_server = None
_pending = None
_start_paused = False
_state = dict(mode=None, phase='menu', value=None, frame=None, revision=0)
_state_lock = threading.Lock()
_last_input = time.monotonic()


class SelectMode(Exception):
    def __init__(self, command, connection):
        self.command, self.connection = command, connection


def validate(command):
    if not isinstance(command, dict) or command.get('mode') not in MODES:
        raise ValueError('Select a mode.')
    action = command.get('action', 'select')
    if action not in ('select', 'set_time', 'start', 'pause', 'lap_plus', 'lap_minus', 'lap_set'):
        raise ValueError('Choose a clock control.')
    value = command.get('time')
    if action == 'set_time' or value is not None:
        if command['mode'] not in (MODES[0], MODES[1], MODES[3]):
            raise ValueError('This mode does not use a start time.')
        import re
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}:[0-5][0-9]:[0-5][0-9]', value):
            raise ValueError('Enter a time as HH:MM:SS.')
        if command['mode'] == MODES[3] and value == '00:00:00':
            raise ValueError('Set a round time greater than zero.')
    result = dict(mode=command['mode'], time=value, action=action)
    if action in ('start', 'pause') and command['mode'] not in (MODES[0], MODES[1], MODES[3]):
        raise ValueError('Select a timer mode first.')
    if action.startswith('lap_'):
        if command['mode'] != 'Lap Counter':
            raise ValueError('Select Lap Counter first.')
        if action == 'lap_set':
            number = command.get('number')
            if type(number) is not int or not 0 <= number <= 999999:
                raise ValueError('Enter a lap number from 0 to 999999.')
            result['number'] = number
    return result


def report(mode=None, phase=None, value=None):
    with _state_lock:
        if mode is not None:
            _state['mode'] = mode
        if phase is not None:
            _state['phase'] = phase
        _state['value'] = value


def capture_frame(image):
    # Called only after the physical display accepts the same logical frame.
    rgb = image.convert('RGB')
    pixels = rgb.tobytes()
    with _state_lock:
        if _state.get('_pixels') == (rgb.size, pixels):
            return
        buffer = io.BytesIO()
        rgb.save(buffer, format='PNG')
        _state.update(frame=base64.b64encode(buffer.getvalue()).decode(),
                      revision=_state['revision'] + 1, _pixels=(rgb.size, pixels))


def snapshot():
    with _state_lock:
        result = {key: value for key, value in _state.items() if not key.startswith('_')}
    result['connected'] = time.monotonic() - _last_input < 4
    if not result['connected']:
        result.update(frame=None, phase='unavailable', value=None)
    return result


def initial_running():
    global _start_paused
    running = not _start_paused
    _start_paused = False
    return running


def start():
    global _server
    if _server is not None:
        return
    server = socket.socket(socket.AF_UNIX)
    # Never replace a live controller's socket.
    if SOCKET_PATH.exists():
        with socket.socket(socket.AF_UNIX) as probe:
            try:
                probe.connect(str(SOCKET_PATH))
            except ConnectionRefusedError:
                SOCKET_PATH.unlink()
            else:
                raise RuntimeError('Controller already running')
    server.bind(str(SOCKET_PATH))
    owner = SOCKET_PATH.parent.stat()
    if os.geteuid() == 0:
        os.chown(SOCKET_PATH, owner.st_uid, owner.st_gid)
    os.chmod(SOCKET_PATH, 0o600)
    server.listen(4)
    _server = server

    def serve():
        while True:
            connection, _ = server.accept()
            connection.settimeout(3)
            try:
                data = bytearray()
                while b'\n' not in data and len(data) <= 1024:
                    part = connection.recv(1024)
                    if not part:
                        break
                    data.extend(part)
                raw = json.loads(data)
                if isinstance(raw, dict) and raw.get('action') == 'status':
                    connection.sendall(json.dumps(snapshot()).encode() + b'\n')
                    connection.close()
                    continue
                command = validate(raw)
                _commands.put_nowait((command, connection, time.monotonic() + 4))
            except (OSError, ValueError, queue.Full):
                connection.close()
    threading.Thread(target=serve, daemon=True).start()


def read_key(timeout=0.05):
    global _last_input, _pending
    _last_input = time.monotonic()
    try:
        command, connection, deadline = _commands.get_nowait()
    except queue.Empty:
        pass
    else:
        if time.monotonic() <= deadline:
            action = command.get('action', 'select')
            if action in ('select', 'set_time'):
                raise SelectMode(command, connection)
            current = snapshot()
            error = None
            if current['mode'] != command['mode']:
                error = 'Select this mode first.'
            elif action in ('start', 'pause') and current['phase'] not in ('ready', 'running', 'paused'):
                error = 'Set the time first.'
            elif action == 'start' and current['mode'] == 'Count Down' and current['value'] == 0:
                error = 'Set a time greater than zero.'
            ready(error='Selection replaced.')
            _pending = SelectMode(command, connection)
            if error:
                ready(error=error)
                return None
            return 'lap_set:' + str(command['number']) if action == 'lap_set' else action
        connection.close()
    try:
        return _keys.get_nowait()
    except queue.Empty:
        pass
    if not sys.stdin.isatty():
        threading.Event().wait(timeout)
        return None
    if select.select([sys.stdin], [], [], timeout)[0]:
        return os.read(sys.stdin.fileno(), 1).decode(errors='ignore')
    return None


def apply_selection(selection):
    global _start_paused
    _start_paused = selection.command.get("action") == "set_time"
    while not _keys.empty():
        _keys.get_nowait()
    value = selection.command['time']
    if value is not None:
        for character in value.replace(':', '') + '\r':
            _keys.put(character)
    global _pending
    ready(error='Selection replaced.')
    _pending = selection
    return MODES.index(selection.command['mode'])


def ready(stage='running', error=None):
    """Acknowledge only after the requested mode has rendered successfully."""
    global _pending
    if _pending is None:
        return
    if not error and stage == 'entry' and _pending.command['time'] is not None:
        return
    selection, _pending = _pending, None
    action = selection.command.get('action', 'select')
    message = error or ('Time set. Ready to start.' if action == 'set_time' else
                        'Clock started.' if action == 'start' else
                        'Clock paused.' if action == 'pause' else
                        'Lap count updated.' if action.startswith('lap_') else
                        'Time set and started.' if selection.command['time'] is not None else 'Mode selected.')
    try:
        selection.connection.sendall(json.dumps(dict(ok=not bool(error), message=message)).encode() + b'\n')
    except OSError:
        pass
    finally:
        selection.connection.close()
