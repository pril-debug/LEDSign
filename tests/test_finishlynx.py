import socket
import time
import unittest
from modes.Lynx_receiver import Decoder, Receiver, State, apply_message, clock_frame


class FinishLynxTests(unittest.TestCase):
    def test_frames_and_fragmentation(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed(b'\x01=0T\n 12:'), [])
        self.assertEqual(decoder.feed(b'34:56\x03\x04\x01T\n 1:23.4\x03\x04'),
                         ['TM,12:34:56', 'TM,1:23.4'])
        self.assertEqual(decoder.feed(b'\x01T\ninvalid\x03\x04'), [])

    def test_display_and_validation(self):
        state = State()
        apply_message(state, 'TM,12:34:56')
        apply_message(state, 'TM,invalid')
        self.assertEqual(state.clock, '12:34:56')
        for width, height in [(192, 64), (64, 32), (384, 128)]:
            frame = clock_frame(width, height, state.clock)
            bounds = frame.getbbox()
            self.assertIsNotNone(bounds)
            self.assertGreaterEqual(bounds[0], 0)
            self.assertLessEqual(bounds[2], width)
            self.assertTrue(bounds[2]-bounds[0] > width*.7 or bounds[3]-bounds[1] > height*.7)

    def test_real_sockets(self):
        for udp in (True, False):
            receiver = Receiver(0, udp)
            receiver.start()
            self.assertTrue(receiver.ready.wait(2))
            self.assertIsNone(receiver.error)
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
            try:
                client.connect(('127.0.0.1', receiver.port))
                client.send(b'\x01T\n12:34:56\x03\x04')
                self.assertEqual(receiver.q.get(timeout=2), 'TM,12:34:56')
                self.assertTrue(receiver.connected)
                if udp:
                    receiver.last_data -= 6
                    self.assertFalse(receiver.connected)
                else:
                    client.close()
                    deadline = time.monotonic()+2
                    while receiver.connected and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertFalse(receiver.connected)
            finally:
                client.close()
                receiver.stop_evt.set()
                receiver.join(2)
            self.assertFalse(receiver.is_alive())


if __name__ == '__main__':
    unittest.main()

class OriginalScriptTests(unittest.TestCase):
    def test_live_packet_without_soh(self):
        self.assertEqual(Decoder().feed(b'T\n 1:17:07.6  \x03\x04\x00\x00'), ['TM,1:17:07.6'])

    def test_original_results_without_row_delimiter(self):
        packet = b'R\nBoys 1600m\n'
        for place, name, mark in [('1', 'Smith, Alex', '4:23.6'), ('2', 'Jones', '4:24.1')]:
            packet += f'{place:<3}\n{name:<30}\n{mark:<10}'.encode()
        packet += b'\x03\x04'
        state = State()
        for message in Decoder().feed(packet):
            apply_message(state, message)
        self.assertEqual(state.event, 'Boys 1600m')
        self.assertEqual(state.results, [('1', '4:23.6'), ('2', '4:24.1')])
        self.assertEqual(state.results_names, ['Smith, Alex', 'Jones'])

    def test_messages_startlist_and_udp_fragments(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed(b'M\nWel'), [])
        state = State()
        for message in decoder.feed(b'come\n\x03\x04'):
            apply_message(state, message)
        self.assertEqual(state.message, 'Welcome')
        self.assertEqual(state.mode, 'MESSAGE')
        self.assertEqual(decoder.feed(b'S\nEvent 1\n\x03\x04'), ['BOARD,S,Event 1'])

    def test_original_tod_styling(self):
        self.assertEqual(Decoder().feed(b'=0T\n 12:34:56   \xb4I4R0\x03\x04'), ['TM,12:34:56'])


class TimeOfDayPrecisionTests(unittest.TestCase):
    def test_whole_seconds_for_time_of_day(self):
        state = State()
        apply_message(state, 'TM,1:17:07.9')
        self.assertEqual(state.clock, '1:17:07')
        apply_message(state, 'TM,12:34:56.7 PM')
        self.assertEqual(state.clock, '12:34:56 PM')
        apply_message(state, 'TM,1:23.4')
        self.assertEqual(state.clock, '1:23.4')

class EventClockTests(unittest.TestCase):
    def test_loaded_event_then_live_clock(self):
        state = State()
        decoder = Decoder()
        for message in decoder.feed(b'S\nGirls 1600m\n\x03\x04T\n 1:23.4\x03\x04'):
            apply_message(state, message)
        self.assertEqual((state.event, state.clock, state.mode), ('Girls 1600m', '1:23.4', 'HEADER'))
        apply_message(state, 'BOARD,S,Girls 1600m')
        self.assertEqual(state.clock, '1:23.4')
        apply_message(state, 'BOARD,S,Boys 800m')
        self.assertEqual((state.event, state.clock), ('Boys 800m', ''))

    def test_event_and_clock_have_separate_visible_areas(self):
        from modes.Lynx_receiver import event_clock_frame
        image = event_clock_frame(192, 64, 'Girls 1600m Final', '1:23.4')
        self.assertIsNotNone(image.crop((0, 0, 192, 21)).getbbox())
        self.assertIsNotNone(image.crop((0, 21, 192, 64)).getbbox())

class StableClockTests(unittest.TestCase):
    def test_digits_are_tabular_and_centered(self):
        from modes.Lynx_receiver import stable_clock_frame, clock_font
        font, template = clock_font(192, 64, False, 1, False)
        self.assertEqual(len({font.getlength(digit) for digit in '0123456789'}), 1)
        for text in ('8.8', '9.9', '59.9', '1:00.0'):
            image = stable_clock_frame(192, 64, text)
            left, top, right, bottom = image.getbbox()
            self.assertLess(abs((left+right)/2-96), 3)
            self.assertLess(abs((top+bottom)/2-32), 3)
        self.assertGreater(font.size, 30)

    def test_captured_event_ends_with_eot(self):
        decoder = Decoder()
        messages = decoder.feed(b'S\nTesty\n\x04' + b'\x00'*9)
        self.assertEqual(messages, ['BOARD,S,Testy'])
        state = State()
        for message in messages:
            apply_message(state, message)
        self.assertEqual(state.event, 'Testy')
        self.assertEqual(decoder.feed(b'T\n 1:23.4\x03\x04'), ['TM,1:23.4'])


class EventDefaultTests(unittest.TestCase):
    def test_default_clock_and_blue_separator(self):
        from modes.Lynx_receiver import event_clock_frame
        image = event_clock_frame(192, 64, 'Testy', '')
        expected = event_clock_frame(192, 64, 'Testy', '0:00.0')
        self.assertEqual(image.tobytes(), expected.tobytes())
        self.assertTrue(all(image.getpixel((x, y)) == (0, 96, 255) for y in (20, 21) for x in range(192)))
        header = image.crop((0, 0, 192, 20))
        self.assertIsNotNone(header.getbbox())
        self.assertTrue(all(r == 0 and b >= g for r, g, b in header.getdata()))
