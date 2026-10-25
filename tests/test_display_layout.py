import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock
from PIL import Image
from display_layout import default_layout, layout_grid, validate_layout, saved_layout, pack_image


class LayoutTests(unittest.TestCase):
    def test_initial_geometry(self):
        layout = validate_layout(default_layout())
        self.assertEqual(len(layout['panels']), 12)
        self.assertEqual((layout['width']*layout['columns'], layout['height']*layout['rows']), (192,64))
        self.assertEqual(pack_image(Image.new('RGB', (192,64)), layout).size, (768,32))

    def test_validation_rejects_missing_duplicates_invalid(self):
        for change in ('missing', 'chain', 'position', 'side', 'rotation', 'type', 'size'):
            layout = default_layout()
            if change == 'missing': layout['panels'].pop()
            if change == 'chain': layout['panels'][0]['chain'] = 2
            if change == 'position': layout['panels'][0]['x'] = 1
            if change == 'side': layout['panels'][0]['side'] = 3
            if change == 'rotation': layout['panels'][0]['rotation'] = 90
            if change == 'type': layout['panels'][0]['chain'] = True
            if change == 'size': layout['width'] = 63
            with self.subTest(change=change), self.assertRaises(ValueError): validate_layout(layout)

    def test_single_side_and_legacy(self):
        self.assertEqual(len(validate_layout(layout_grid(32,32,2,3,1))['panels']), 6)
        legacy = saved_layout(dict(led_rows=64, led_cols=64, led_chain=2, led_parallel=1))
        self.assertEqual((legacy['columns'], legacy['rows'], legacy['sides']), (2,1,1))

    def test_arbitrary_chain_mirrors_same_content(self):
        layout = default_layout()
        order = [12,3,8,1,9,6,4,11,2,10,5,7]
        image = Image.new('RGB', (192,64))
        for i,p in enumerate(layout['panels']):
            p['chain'] = order[i]
            if i < 6:
                image.paste((20+i,60+i,100+i), (p['x']*64,p['y']*32,p['x']*64+64,p['y']*32+32))
        output = pack_image(image, validate_layout(layout))
        for p in layout['panels']:
            self.assertEqual(output.getpixel(((p['chain']-1)*64,0)), image.getpixel((p['x']*64,p['y']*32)))

    def test_rotation_and_test_markers(self):
        for rotation in (0,90,180,270):
            layout = layout_grid(32,32,1,1,1)
            layout['panels'][0]['rotation'] = rotation
            image = Image.new('RGB', (32,32)); image.putpixel((2,3), (255,0,0))
            self.assertEqual(pack_image(image,validate_layout(layout)).tobytes(), image.rotate(rotation).tobytes())
            test = pack_image(image,layout,test=True)
            self.assertGreater(len(set(test.getdata())), 3)

    def test_matrix_adapter_uses_logical_size_single_output(self):
        fake = types.SimpleNamespace(RGBMatrix=Mock(), RGBMatrixOptions=type('Options',(),{}))
        with patch.dict(sys.modules, rgbmatrix=fake):
            spec = importlib.util.spec_from_file_location('mapped_test', Path(__file__).resolve().parents[1]/'modes/matrix_utils.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, 'APP_ROOT', Path(tmp)):
            matrix = module.build_matrix({'display_layout':default_layout(), 'led_brightness':37, 'led_multiplexing':1})
            options = fake.RGBMatrix.call_args.kwargs['options']
            self.assertEqual((options.rows,options.cols,options.chain_length,options.parallel,options.brightness), (32,64,12,1,37))
            self.assertEqual(options.multiplexing, 1)
            self.assertEqual((matrix.width,matrix.height), (192,64))
            matrix.SetImage(Image.new('RGB',(192,64)))
            self.assertEqual(matrix.matrix.SetImage.call_args.args[0].size, (768,32))
            with self.assertRaises(ValueError): matrix.SetImage(Image.new('RGB',(768,32)))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('display_helper',Path(__file__).resolve().parents[1]/'scripts/web_display.py')
        self.helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.helper)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'display-active.json'
        self.old = json.dumps({'layout':layout_grid(32,32,1,1,1),'test_until':0}).encode()
        self.path.write_bytes(self.old)
        self.patch = patch.object(self.helper,'ACTIVE',self.path); self.patch.start()

    def tearDown(self): self.patch.stop(); self.temp.cleanup()

    def test_apply_and_test_restore(self):
        with patch.object(self.helper.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run, patch.object(self.helper.time,'sleep'):
            self.helper.run({'action':'test','layout':default_layout()})
            self.assertEqual(self.path.read_bytes(),self.old)
            self.assertEqual(sum(c.args[0]==['systemctl','restart','sign-ui'] for c in run.call_args_list),2)
            self.helper.run({'action':'apply','layout':default_layout()})
            self.assertEqual(json.loads(self.path.read_text())['layout'],default_layout())

    def test_stopped_controller_and_failed_restart_do_not_change_layout(self):
        with patch.object(self.helper.subprocess,'run',return_value=types.SimpleNamespace(returncode=3)):
            with self.assertRaises(ValueError): self.helper.run({'action':'apply','layout':default_layout()})
        self.assertEqual(self.path.read_bytes(),self.old)
        with patch.object(self.helper.subprocess,'run',side_effect=[types.SimpleNamespace(returncode=0),RuntimeError('restart'),types.SimpleNamespace(returncode=0)]):
            with self.assertRaises(ValueError): self.helper.run({'action':'apply','layout':default_layout()})
        self.assertEqual(self.path.read_bytes(),self.old)


class ModeRenderingTests(unittest.TestCase):
    def test_existing_modes_and_finishlynx_use_logical_resolution(self):
        from unittest.mock import MagicMock
        import importlib
        fake = types.SimpleNamespace(RGBMatrix=Mock(), RGBMatrixOptions=type('Options',(),{}), graphics=Mock())
        with patch.dict(sys.modules, rgbmatrix=fake):
            utils = importlib.import_module('modes.matrix_utils')
            timer = importlib.import_module('modes.timer_modes')
            laps = importlib.import_module('modes.lap_counter')
            rounds = importlib.import_module('modes.rounds')
            lynx = importlib.import_module('modes.Lynx_receiver')
        matrix = MagicMock(width=192,height=64)
        with patch.object(laps, '_getch', return_value='\x1b'):
            laps.run_lap_counter(matrix)
        self.assertEqual(matrix.SetImage.call_args.args[0].size,(192,64))
        with patch.object(rounds, '_getch', return_value='\x1b'):
            rounds._pause_screen(matrix)
        self.assertEqual(matrix.SetImage.call_args.args[0].size,(192,64))
        timer.draw_time(matrix,123)
        self.assertEqual(matrix.SetImage.call_args.args[0].size,(192,64))
        with patch.dict(sys.modules, rgbmatrix=fake, **{'modes.matrix_utils': utils}), patch.object(utils,'build_matrix',return_value=matrix):
            renderer=lynx.Renderer({})
            state=lynx.State(); state.event='Test'; state.clock='1:23'; renderer.render(state)
            self.assertEqual(matrix.SetImage.call_args.args[0].size,(192,64))
            state.mode='RESULTS'; state.results=[('1','4:23')]; state.results_names=['Runner']; renderer.render(state)
            self.assertEqual(matrix.SetImage.call_args.args[0].size,(192,64))
