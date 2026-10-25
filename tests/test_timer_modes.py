import importlib
import types
import unittest
from unittest.mock import Mock, patch


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.timer = importlib.import_module('modes.timer_modes')

    def test_compact_clock(self):
        for seconds, expected in [(0, '0'), (9, '9'), (59, '59'), (60, '1:00'),
                                  (756, '12:36'), (3600, '1:00:00'), (360001, '100:00:01')]:
            self.assertEqual(self.timer.clock_text(seconds), expected)

    def test_clock_pixels_centered_and_unclipped(self):
        for width, height in [(32, 16), (128, 64), (192, 64), (384, 128)]:
            matrix = Mock(width=width, height=height)
            for seconds in (0, 11, 756, 3600, 359999):
                self.timer.draw_time(matrix, seconds)
                frame = matrix.SetImage.call_args.args[0]
                left, top, right, bottom = frame.getbbox()
                self.assertGreater(left, 0)
                self.assertGreater(top, 0)
                self.assertLess(right, width)
                self.assertLess(bottom, height)
                self.assertLessEqual(abs(left-(width-right)), 3)
                self.assertLessEqual(abs(top-(height-bottom)), 2)

    def test_entry_marks_next_digit_red(self):
        matrix = types.SimpleNamespace(width=192, height=64)
        positions = []
        for digits in ('', '0', '00', '001', '0012', '00123'):
            frame = self.timer.entry_frame(matrix, digits, 'Set time HH:MM:SS')
            red = [(x, y) for y in range(64) for x in range(192)
                   if frame.getpixel((x, y))[0] > 0 and frame.getpixel((x, y))[1:] == (0, 0)]
            self.assertTrue(red)
            positions.append(min(x for x, y in red))
        self.assertEqual(positions, sorted(set(positions)))

    def test_time_entry_delete_validation_and_cancel(self):
        matrix = Mock(width=192, height=64)
        for keys, expected in [('001236\n', 756), ('001236\b6\n', 756), ('0061236\n', 756), ('\n', 0), ('\x1b', None)]:
            with patch.object(self.timer, 'getch', side_effect=list(keys)):
                self.assertEqual(self.timer.collect_hhmmss(matrix), expected)

    def test_enter_starts_both_directions_without_second_enter(self):
        for direction, initial, expected in [(1, 0, 1), (-1, 2, 1), (-1, 1, 0)]:
            with patch.object(self.timer, 'collect_hhmmss', return_value=initial), \
                 patch.object(self.timer, 'draw_time') as draw, \
                 patch.object(self.timer, '_get_key_nonblock', side_effect=[None, KeyboardInterrupt]), \
                 patch.object(self.timer.time, 'monotonic', side_effect=[0, 1.1]):
                with self.assertRaises(KeyboardInterrupt):
                    self.timer.run_clock(object(), direction)
                self.assertEqual([c.args[1] for c in draw.call_args_list], [initial, expected])

    def test_resume_does_not_count_paused_time(self):
        with patch.object(self.timer, 'collect_hhmmss', return_value=0), \
             patch.object(self.timer, 'draw_time') as draw, \
             patch.object(self.timer, '_get_key_nonblock', side_effect=[' ', None, '\n', None, KeyboardInterrupt]), \
             patch.object(self.timer.time, 'sleep'), \
             patch.object(self.timer.time, 'monotonic', side_effect=[0, 0.2, 100, 100.1, 100.1, 101.2]):
            with self.assertRaises(KeyboardInterrupt):
                self.timer.run_clock(object())
            self.assertEqual([c.args[1] for c in draw.call_args_list], [0, 1])

    def test_rounds_use_compact_frame_and_three_second_border(self):
        rounds = importlib.import_module('modes.rounds')
        matrix = Mock(width=192, height=64)
        for remaining in (1, 2, 3, 4, 11, 756):
            with patch.object(rounds, 'collect_hhmmss', return_value=remaining), \
                 patch.object(rounds, '_getch', return_value='\x1b') as key:
                rounds.run_rounds(matrix)
                key.assert_called_once()
            actual = matrix.SetImage.call_args.args[0]
            expected = self.timer.time_frame(matrix, remaining, padding=7 if remaining <= 3 else None)
            if remaining <= 3:
                from PIL import ImageDraw
                ImageDraw.Draw(expected).rectangle((0, 0, 191, 63), outline='white' if remaining == 2 else 'red', width=5)
            self.assertEqual(actual.tobytes(), expected.tobytes())


    def test_laps_fill_height_and_preserve_numeric_count(self):
        laps = importlib.import_module('modes.lap_counter')
        matrix = Mock(width=192, height=64)
        with patch.object(laps, '_getch', side_effect=['+']*61+['-', '\x1b']):
            laps.run_lap_counter(matrix)
        frames = [call.args[0] for call in matrix.SetImage.call_args_list]
        self.assertEqual(len(frames), 63)
        self.assertGreaterEqual(frames[0].getbbox()[3]-frames[0].getbbox()[1], 58)
        self.assertEqual(frames[-1].tobytes(), self.timer.number_frame(matrix, '60').tobytes())
        for text in ('1', '123', '1234567'):
            frame = self.timer.number_frame(matrix, text)
            left, top, right, bottom = frame.getbbox()
            self.assertGreater(left, 0)
            self.assertGreater(top, 0)
            self.assertLess(right, 192)
            self.assertLess(bottom, 64)
