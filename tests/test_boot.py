import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from PIL import Image


class BootScreenTests(unittest.TestCase):
    def setUp(self):
        # boot.splash imports the helper directly; isolate normal menu tests.
        with patch.dict('sys.modules', rgbmatrix=types.SimpleNamespace(RGBMatrix=Mock(), RGBMatrixOptions=Mock())):
            boot = importlib.import_module('boot.splash')
        self.hotspot = patch.object(boot, 'hotspot_status', return_value=None)
        self.hotspot.start()
        self.addCleanup(self.hotspot.stop)

    def test_logo_and_address_share_frame(self):
        with patch.dict('sys.modules', rgbmatrix=types.SimpleNamespace(RGBMatrix=Mock(), RGBMatrixOptions=Mock())):
            boot = importlib.import_module('boot.splash')
        with tempfile.TemporaryDirectory() as directory:
            logo = Path(directory)/'logo.png'
            Image.new('RGBA', (100, 40), 'red').save(logo)
            with patch.object(boot, 'find_logo', return_value=logo):
                frame = boot.boot_frame(types.SimpleNamespace(width=192, height=64), '192.168.100.100')
            self.assertEqual(frame.size, (192, 64))
            red = [(x,y) for y in range(64) for x in range(192) if frame.getpixel((x,y)) == (255,0,0)]
            white = [(x,y) for y in range(64) for x in range(192) if frame.getpixel((x,y))[1] > 0]
            self.assertTrue(red)
            self.assertTrue(white)
            self.assertLess(max(y for x,y in red), min(y for x,y in white))
            self.assertGreaterEqual(max(x for x,y in white), 187)
            self.assertLessEqual(max(x for x,y in white), 189)
            self.assertEqual(max(y for x,y in white), 61)

    def test_missing_network_and_logo(self):
        with patch.dict('sys.modules', rgbmatrix=types.SimpleNamespace(RGBMatrix=Mock(), RGBMatrixOptions=Mock())):
            boot = importlib.import_module('boot.splash')
        with patch.object(boot, 'find_logo', return_value=None):
            frame = boot.boot_frame(types.SimpleNamespace(width=192,height=64), None)
        self.assertIsNotNone(frame.getbbox())

    def test_packaged_default_customer_priority_and_legacy_fallback(self):
        with patch.dict('sys.modules', rgbmatrix=types.SimpleNamespace(RGBMatrix=Mock(), RGBMatrixOptions=Mock())):
            boot = importlib.import_module('boot.splash')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            customer=root/'customer.png'
            legacy=root/'old-default.png'
            default=root/'FlexClock-Light.png'
            for path in (legacy,default):Image.new('RGB',(64,32),'blue').save(path)
            conf=dict(customer_logo_path=str(customer),logo_path=str(legacy))
            with patch.object(boot,'APP_ROOT',root),patch.object(boot,'CONF',conf):
                self.assertEqual(boot.find_logo(),default)
                Image.new('RGB',(64,32),'red').save(customer)
                self.assertEqual(boot.find_logo(),customer)
                customer.unlink();default.unlink()
                self.assertEqual(boot.find_logo(),legacy)

class HotspotDisplayTests(unittest.TestCase):
    def setUp(self):
        with patch.dict('sys.modules', rgbmatrix=types.SimpleNamespace(RGBMatrix=Mock(), RGBMatrixOptions=Mock())):
            self.utils = importlib.import_module('modes.matrix_utils')
            self.boot = importlib.import_module('boot.splash')

    def test_hotspot_footer_keeps_logo_mode_and_both_details(self):
        from PIL import ImageDraw
        labels = []
        actual = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, *args, **kwargs):
            labels.append(text)
            return actual(draw, xy, text, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            logo = Path(directory)/'logo.png'
            Image.new('RGBA', (100,40), 'red').save(logo)
            with patch.object(self.boot, 'find_logo', return_value=logo), patch.object(self.boot, 'hotspot_status', return_value=dict(ssid='FlexClock-ABCDEF', url='http://192.168.4.1')), patch.object(ImageDraw.ImageDraw, 'text', capture):
                for width,height in [(32,16),(64,32),(192,64),(384,128)]:
                    frame = self.boot.boot_frame(types.SimpleNamespace(width=width,height=height), '192.168.4.1', 'Count Down')
                    self.assertEqual(frame.size,(width,height))
                    red = [(x,y) for y in range(height) for x in range(width) if frame.getpixel((x,y)) == (255,0,0)]
                    yellow = [(x,y) for y in range(height) for x in range(width) if (lambda c: c[0] == c[1] and c[0] > 0 and c[2] == 0)(frame.getpixel((x,y)))]
                    self.assertTrue(red)
                    self.assertTrue(yellow)
                    self.assertLess(max(y for x,y in red), min(y for x,y in yellow))
        for label in ('> Count Down','FlexClock-ABCDEF','http://192.168.4.1'):
            self.assertIn(label,labels)
        self.assertNotIn('Wi-Fi setup',labels)

    def test_ethernet_then_wifi_then_hotspot_display_priority(self):
        hotspot = dict(ssid='FlexClock-ABCDEF', url='http://192.168.4.1')
        matrix = types.SimpleNamespace(width=192, height=64)
        with patch.object(self.boot, 'hotspot_status', return_value=hotspot), patch.object(self.boot, 'hotspot_menu_frame', wraps=self.boot.hotspot_menu_frame) as footer:
            for addresses, expected in [({'eth0':'10.0.0.2','wlan0':'192.168.4.1'},'10.0.0.2'),
                                        ({'eth0':None,'wlan0':'10.0.1.2'},'10.0.1.2'),
                                        ({'eth0':None,'wlan0':'192.168.4.1'},'192.168.4.1')]:
                with patch.object(self.utils, 'iface_ipv4', side_effect=addresses.get):
                    iface, ip = self.utils.get_primary_ip()
                self.assertEqual(ip, expected)
                footer.reset_mock()
                self.boot.boot_frame(matrix, ip)
                self.assertEqual(footer.called, ip == '192.168.4.1')

    def test_active_modes_are_never_replaced_by_hotspot_frame(self):
        from display_layout import layout_grid
        matrix = self.utils.MappedMatrix.__new__(self.utils.MappedMatrix)
        matrix.width,matrix.height = 64,32
        matrix.layout = layout_grid(64,32,1,1,1)
        matrix.test_until = 0
        matrix.matrix = Mock()
        with patch.object(Path,'read_text',return_value='{"mode":"hotspot","ssid":"FlexClock-ABCDEF"}'), patch('modes.control.capture_frame') as capture:
            for color in ('red','green','blue','white','yellow'):
                original = Image.new('RGB',(64,32),color)
                matrix.SetImage(original)
                self.assertEqual(matrix.matrix.SetImage.call_args.args[0].tobytes(),original.tobytes())
                self.assertIs(capture.call_args.args[0],original)

    def test_invalid_or_inactive_hotspot_is_ignored(self):
        for value in ('{"mode":"connecting"}', 'invalid json', 'null'):
            with patch.object(Path,'read_text',return_value=value):
                self.assertIsNone(self.utils.hotspot_status())
        with patch.object(Path,'read_text',side_effect=FileNotFoundError):
            self.assertIsNone(self.utils.hotspot_status())
