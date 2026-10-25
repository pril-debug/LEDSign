import importlib.util
import json
from pathlib import Path
import queue
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from modes import control, timer_modes, menu

spec = importlib.util.spec_from_file_location('dashboard_controller', Path(__file__).resolve().parents[1]/'web/controller.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class DashboardControl(unittest.TestCase):
    def tearDown(self):
        control.ready(error='Test ended.')
        while not control._keys.empty():
            control._keys.get_nowait()

    def test_validation(self):
        for mode, value in [('Unknown', None), ('Count Up', '00:60:00'),
                            ('Round Counter', '00:00:00'), ('Lap Counter', '00:00:01')]:
            with self.assertRaises(ValueError):
                client.select_mode(mode, value)
            with self.assertRaises(ValueError):
                control.validate(dict(mode=mode, time=value))

    def test_real_socket_time_entry_and_render_acknowledgement(self):
        # Isolate the listener from the installed controller and device display.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'controller.sock'
            with patch.object(control, 'SOCKET_PATH', path), patch.object(control, '_server', None), \
                 patch.object(client, 'SOCKET_PATH', path):
                control.start()
                for mode, seconds in [('Count Up', 62), ('Count Down', 62), ('Round Counter', 62)]:
                    reply = queue.Queue()
                    def request():
                        try:
                            reply.put(client.select_mode(mode, '00:01:02'))
                        except Exception as error:
                            reply.put(error)
                    worker = threading.Thread(target=request)
                    worker.start()
                    command, connection, deadline = control._commands.get(timeout=2)
                    control._commands.put((command, connection, deadline))
                    with self.assertRaises(control.SelectMode) as raised:
                        control.read_key(0)
                    self.assertEqual(control.apply_selection(raised.exception), control.MODES.index(mode))
                    matrix = Mock(width=192, height=64)
                    self.assertEqual(timer_modes.collect_hhmmss(matrix), seconds)
                    self.assertTrue(reply.empty(), 'Must not acknowledge start while still entering time')
                    timer_modes.draw_time(matrix, seconds)
                    control.ready()
                    worker.join(2)
                    self.assertEqual(reply.get(timeout=1)['message'], 'Time set. Ready to start.')
                    self.assertTrue(matrix.SetImage.called)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_selection_waits_for_render_and_reports_failure(self):
        for error in (None, 'Unable to start mode.'):
            a, b = socket.socketpair()
            with a, b:
                control.apply_selection(control.SelectMode(dict(mode='FinishLynx', time=None), a))
                control.ready(error=error)
                self.assertEqual(json.loads(b.recv(1024))['ok'], error is None)

    def test_set_start_pause_laps_and_actual_preview(self):
        import time
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'controller.sock'
            with patch.object(control, 'SOCKET_PATH', path), patch.object(control, '_server', None), patch.object(client, 'SOCKET_PATH', path):
                control.start()
                stop = threading.Event()
                failures = queue.Queue()
                matrix = Mock(width=192, height=64)
                matrix.SetImage.side_effect = control.capture_frame
                def dispatch():
                    selection = None
                    try:
                        while not stop.is_set():
                            try:
                                if selection is None:
                                    control.read_key(0.01)
                                else:
                                    index = control.apply_selection(selection)
                                    selection = None
                                    menu.launch_mode(matrix, index)
                            except control.SelectMode as command:
                                selection = command
                    except Exception as error:
                        failures.put(error)
                worker = threading.Thread(target=dispatch, daemon=True)
                worker.start()
                try:
                    for mode in ('Count Up', 'Count Down', 'Round Counter'):
                        reply = client.command('set_time', mode, '00:00:05')
                        self.assertTrue(reply['ok'])
                        self.assertEqual(client.status()['value'], 5)
                        self.assertIn(client.status()['phase'], ('ready', 'paused'))
                        time.sleep(1.1)
                        self.assertEqual(client.status()['value'], 5, 'Setting time must leave the clock stopped')
                        self.assertTrue(client.command('start', mode)['ok'])
                        self.assertTrue(client.command('start', mode)['ok'], 'Repeated Start must not toggle pause')
                        self.assertEqual(client.status()['phase'], 'running')
                        deadline = time.monotonic()+3
                        while client.status()['value'] == 5 and time.monotonic() < deadline:
                            time.sleep(0.02)
                        current = client.status()['value']
                        self.assertGreater(current, 5) if mode == 'Count Up' else self.assertLess(current, 5)
                        client.command('pause', mode)
                        self.assertIn(client.status()['phase'], ('ready', 'paused'))
                    client.command('select', 'Lap Counter')
                    client.command('lap_set', 'Lap Counter', number='42')
                    self.assertEqual(client.status()['value'], 42)
                    client.command('lap_plus', 'Lap Counter')
                    self.assertEqual(client.status()['value'], 43)
                    client.command('lap_minus', 'Lap Counter')
                    self.assertEqual(client.status()['value'], 42)
                    client.command('lap_set', 'Lap Counter', number='0')
                    client.command('lap_minus', 'Lap Counter')
                    self.assertEqual(client.status()['value'], 0)
                    control._keys.put('+')  # Physical keyboard uses the same rendering/status path.
                    deadline = time.monotonic()+2
                    while client.status()['value'] != 1 and time.monotonic() < deadline:
                        time.sleep(0.02)
                    snapshot = client.status()
                    self.assertEqual(snapshot['value'], 1)
                    import base64
                    import io
                    frame = Image.open(io.BytesIO(base64.b64decode(snapshot['frame'])))
                    self.assertEqual(frame.tobytes(), timer_modes.number_frame(matrix, '1').tobytes())
                    with self.assertRaises(RuntimeError):
                        client.command('start', 'Count Up')
                    self.assertTrue(failures.empty())
                finally:
                    stop.set()
                    control._keys.put('\x1b')
                    control._keys.put('\x1b')
                    worker.join(3)
                    self.assertFalse(worker.is_alive())

    def test_preview_expires_when_input_loop_stops(self):
        import time
        with patch.object(control, '_last_input', time.monotonic()-10):
            self.assertFalse(control.snapshot()['connected'])
            self.assertIsNone(control.snapshot()['frame'])
