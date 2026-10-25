import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from PIL import Image


class BootScreenTests(unittest.TestCase):
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
