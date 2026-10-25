import importlib
import os
import pty
import socket
import termios
import tty
import types
import unittest
from unittest.mock import Mock, patch


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.menu = importlib.import_module('modes.menu')
        self.boot = importlib.import_module('boot.splash')

    def test_real_terminal_arrow_sequences_and_enter(self):
        master, slave = pty.openpty()
        try:
            tty.setcbreak(slave)
            with os.fdopen(os.dup(slave)) as stream, patch.object(self.menu.sys, 'stdin', stream):
                for sequence, expected in [(b'\x1b[A', 'up'), (b'\x1b[B', 'down'), (b'\x1bOA', 'up'), (b'+', '+'), (b'-', '-'), (b'\r', '\n')]:
                    os.write(master, sequence)
                    self.assertEqual(self.menu.getch(), expected)
        finally:
            os.close(master)
            os.close(slave)

    def test_default_idle_wrap_and_launch_every_mode(self):
        master, slave = pty.openpty()
        frames, launched = [], []
        keys = [None, '-', '\n', '+', '\n'] + ['down', '\n'] * 4
        def next_key():
            if keys:
                return keys.pop(0)
            raise KeyboardInterrupt
        try:
            with os.fdopen(os.dup(slave)) as stream, patch.object(self.menu.sys, 'stdin', stream), \
                 patch.object(self.menu, 'getch', side_effect=next_key), \
                 patch.object(self.menu.control, 'start'), \
                 patch.object(self.menu, 'get_primary_ip', return_value=('eth0', '10.0.0.1')), \
                 patch.object(self.menu, 'push'), \
                 patch.object(self.boot, 'boot_frame', side_effect=lambda m, ip, mode, msg: frames.append(mode)), \
                 patch.object(self.menu, 'launch_mode', side_effect=lambda m, i: launched.append(i)):
                with self.assertRaises(KeyboardInterrupt):
                    self.menu.run_menu(object())
            self.assertEqual(frames[:2], ['Count Up', 'Count Up'])
            self.assertEqual(launched, [4, 0, 1, 2, 3, 4])
            self.assertEqual(termios.tcgetattr(slave)[3] & termios.ICANON, termios.ICANON)
        finally:
            os.close(master)
            os.close(slave)

    def test_dispatch_reuses_modes_and_matrix(self):
        matrix = object()
        targets = [('timer_modes', 'run_clock'), ('timer_modes', 'run_clock'),
                   ('lap_counter', 'run_lap_counter'), ('rounds', 'run_rounds'), ('Lynx_receiver', 'main')]
        for index, (module, function) in enumerate(targets):
            mode = importlib.import_module('modes.' + module)
            with patch.object(mode, function) as launch:
                self.menu.launch_mode(matrix, index)
                launch.assert_called_once()
                self.assertIn(matrix, list(launch.call_args.args) + list(launch.call_args.kwargs.values()))

    def test_footers_and_logo_fit_supported_sizes(self):
        from PIL import Image
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            logo = Path(directory) / 'logo.png'
            Image.new('RGBA', (100, 40), 'red').save(logo)
            with patch.object(self.boot, 'find_logo', return_value=logo):
                for width, height in [(32, 16), (64, 32), (128, 64), (192, 64), (384, 128)]:
                    for label in self.menu.MENU:
                        frame = self.boot.boot_frame(types.SimpleNamespace(width=width, height=height), '192.168.100.100', label)
                        pixels = list(frame.getdata())
                        self.assertEqual(frame.size, (width, height))
                        for color in [(255, 0, 0), (255, 255, 0), (255, 255, 255)]:
                            self.assertTrue(any((r > 0 and g == 0 and b == 0) if color == (255, 0, 0) else
                                                (r == g and r > 0 and b == 0) if color == (255, 255, 0) else
                                                (r == g == b and r > 0) for r, g, b in pixels), (width, height, label, color))

    def test_finishlynx_receiver_releases_port(self):
        from modes.Lynx_receiver import Receiver
        for _ in range(2):
            receiver = Receiver(0, True)
            receiver.start()
            self.assertTrue(receiver.ready.wait(2))
            self.assertIsNone(receiver.error)
            sock = receiver.sockets[0]
            port = sock.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b'TM,2:03.7\n', ('127.0.0.1', port))
            self.assertEqual(receiver.q.get(timeout=2), 'TM,2:03.7')
            receiver.stop_evt.set()
            receiver.join(2)
            self.assertFalse(receiver.is_alive())
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as check:
                check.bind(('127.0.0.1', port))
