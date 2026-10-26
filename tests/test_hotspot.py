import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('hotspot_network', Path(__file__).resolve().parents[1] / 'scripts/web_network.py')
net = importlib.util.module_from_spec(spec)
spec.loader.exec_module(net)


class HotspotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(net, 'HOTSPOT_STATE', root/'state'),
                        patch.object(net, 'HOTSPOT_ENABLED', root/'enabled'),
                        patch.object(net, 'WIFI_PAUSED', root/'paused'),
                        patch.object(net, 'require_hotspot_stack')]
        for p in self.patches: p.start()
        net.HOTSPOT_ENABLED.touch()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.temp.cleanup()

    def test_serial_is_hardware_based_and_validated(self):
        with patch.object(Path, 'read_bytes', return_value=b'00000000abcdef12\x00'):
            self.assertEqual(net.hotspot_ssid(), 'FlexClock-CDEF12')
            self.assertEqual(net.hotspot_ssid(), 'FlexClock-CDEF12')
        with patch.object(Path, 'read_bytes', side_effect=FileNotFoundError), patch.object(Path, 'read_text', return_value='Serial : 000000001234abcd\n'):
            self.assertEqual(net.hotspot_ssid(), 'FlexClock-34ABCD')
        for value in (b'0000000000000000', b'not-a-serial'):
            with patch.object(Path, 'read_bytes', return_value=value), self.assertRaises(ValueError):
                net.hotspot_ssid()

    def test_ap_never_saved_or_touches_ethernet(self):
        calls = []
        def wpa(*args):
            calls.append(args)
            return {'get_capability':'IBSS AP', 'add_network':'9'}.get(args[0], 'OK')
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'hotspot_ssid',return_value='FlexClock-ABCDEF'), patch.object(net,'ipv4_addresses',return_value=[]), patch.object(net,'wpa',side_effect=wpa), patch.object(net,'station_status',return_value={'mode':'AP','wpa_state':'COMPLETED'}), patch.object(net,'active',return_value=True), patch.object(net,'run') as run:
            net.hotspot_start()
        self.assertNotIn(('save_config',), calls)
        self.assertIn(('set_network','9','key_mgmt','NONE'), calls)
        self.assertIn(('set_network','9','mode','2'), calls)
        self.assertEqual(json.loads(net.HOTSPOT_STATE.read_text())['mode'], 'hotspot')
        commands = [c.args[0] for c in run.call_args_list]
        self.assertIn(['dhcpcd','-k','wlan0'],commands)
        self.assertIn(['ip','address','add','192.168.4.1/24','dev','wlan0'],commands)
        self.assertFalse(any('eth0' in c or 'restart' in c for c in commands))

    def test_failed_connection_and_dhcp_failure_restore_ap(self):
        for result in (ValueError('wrong password'), {'connected':True,'ip':None}):
            with patch.object(net,'hotspot_active',return_value=True), patch.object(net,'hotspot_stop') as stop, patch.object(net,'hotspot_start') as start, patch.object(net,'wifi',side_effect=[result]):
                with self.assertRaises(ValueError):
                    net.wifi_with_fallback({'ssid':'Test','security':'open'})
                stop.assert_called_once()
                start.assert_called_once()

    def test_invalid_input_does_not_remove_hotspot(self):
        with patch.object(net,'hotspot_stop') as stop:
            with self.assertRaises(ValueError): net.wifi_with_fallback({'ssid':'','security':'open'})
            stop.assert_not_called()

    def test_address_failure_preserves_saved_credentials(self):
        for address in (None, '169.254.10.2', net.HOTSPOT_IP):
            calls = []
            def wpa(*args):
                calls.append(args)
                return {'ping': 'PONG', 'add_network': '2',
                        'list_networks': 'header\n0\tTest\tany\t[CURRENT]',
                        'status': 'wpa_state=COMPLETED\nid=2'}.get(args[0], 'OK')
            with patch.object(net, 'require_stack'), patch.object(net, 'wpa', side_effect=wpa), patch.object(net, 'wifi_events'), patch.object(net, 'authentication_failed', return_value=False), patch.object(net, 'run'), patch.object(net, 'wait_address', return_value=address):
                with self.assertRaisesRegex(ValueError, 'No Wi-Fi address'):
                    net.wifi({'ssid': 'Test', 'security': 'open'})
            self.assertNotIn(('save_config',), calls)
            self.assertNotIn(('remove_network', '0'), calls)
            self.assertIn(('remove_network', '2'), calls)
            self.assertIn(('reconfigure',), calls)

    def test_credentials_committed_only_after_usable_address(self):
        calls = []
        def wpa(*args):
            calls.append(args)
            return {'ping': 'PONG', 'add_network': '2',
                    'list_networks': 'header\n0\tTest\tany\t[CURRENT]',
                    'status': 'wpa_state=COMPLETED\nid=2'}.get(args[0], 'OK')
        def address(*args):
            self.assertNotIn(('save_config',), calls)
            self.assertNotIn(('remove_network', '0'), calls)
            return '192.168.0.20'
        with patch.object(net, 'require_stack'), patch.object(net, 'wpa', side_effect=wpa), patch.object(net, 'wifi_events'), patch.object(net, 'authentication_failed', return_value=False), patch.object(net, 'run'), patch.object(net, 'wait_address', side_effect=address):
            result = net.wifi({'ssid': 'Test', 'security': 'open'})
        self.assertEqual(result['ip'], '192.168.0.20')
        self.assertIn(('remove_network', '0'), calls)
        self.assertIn(('save_config',), calls)

    def test_disconnect_stays_in_hotspot_and_success_clears_pause(self):
        with patch.object(net,'hotspot_active',return_value=False), patch.object(net,'wifi',return_value={'connected':False}), patch.object(net,'hotspot_start'):
            net.wifi_with_fallback({'disconnect':True})
        self.assertTrue(net.WIFI_PAUSED.exists())
        net.HOTSPOT_STATE.touch()
        with patch.object(net,'hotspot_active',return_value=True), patch.object(net,'hotspot_stop'), patch.object(net,'wifi',return_value={'connected':True,'ip':'10.0.0.2'}):
            net.wifi_with_fallback({'ssid':'test','security':'open'})
        self.assertFalse(net.HOTSPOT_STATE.exists())
        self.assertFalse(net.WIFI_PAUSED.exists())

    def test_outage_grace_and_connected_reset(self):
        monitor = net.HotspotMonitor()
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'station_status',return_value={}), patch.object(net,'station_ready',return_value=False), patch.object(net.time,'monotonic',side_effect=[0,29,30,30]), patch.object(net,'hotspot_start') as start:
            monitor.tick(); monitor.tick()
            start.assert_not_called()
            monitor.tick()
            start.assert_called_once()
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'station_status',return_value={}), patch.object(net,'station_ready',return_value=True):
            monitor.tick()
            self.assertIsNone(monitor.lost_since)

    def test_clients_protect_hotspot_and_failed_idle_retry_restores(self):
        monitor = net.HotspotMonitor()
        state = {'mode':'AP','wpa_state':'COMPLETED'}
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'station_status',return_value=state), patch.object(net,'ipv4_addresses',return_value=[{'local':net.HOTSPOT_IP}]), patch.object(net,'active',return_value=True), patch.object(net,'run',return_value='Station abc'), patch.object(net,'hotspot_stop') as stop:
            monitor.next_retry = 0
            monitor.tick()
            stop.assert_not_called()
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'station_status',return_value=state), patch.object(net,'ipv4_addresses',return_value=[{'local':net.HOTSPOT_IP}]), patch.object(net,'active',return_value=True), patch.object(net,'run',return_value=''), patch.object(net,'wpa',return_value='header\n0\tSaved\tany\t[DISABLED]\n1\tFlexClock\tany\t[CURRENT]'), patch.object(net,'hotspot_stop') as stop, patch.object(net,'hotspot_start') as start, patch.object(net,'station_ready',return_value=False), patch.object(net.time,'monotonic',side_effect=[400,400,431]):
            monitor.next_retry = 0
            monitor.tick()
            stop.assert_called_once(); start.assert_called_once()

    def test_partial_transition_repaired(self):
        net.publish_hotspot('starting','FlexClock-ABCDEF')
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'station_status',return_value={}), patch.object(net,'hotspot_start') as start:
            net.HotspotMonitor().tick()
            start.assert_called_once()

    def test_installer_preserves_other_settings_and_is_idempotent(self):
        path = Path(self.temp.name)/'dhcpcd.conf'
        original = ('hostname\ninterface eth0\nstatic ip_address=10.0.0.2/24\n'
                    '# LEDSign wlan0 BEGIN\ninterface wlan0\nmetric 3000\n'
                    'static ip_address=10.1.0.2/24\n# LEDSign wlan0 END\n')
        path.write_text(original)
        net.prepare_hotspot_config(path)
        result = path.read_text()
        self.assertIn('interface eth0\nstatic ip_address=10.0.0.2/24',result)
        self.assertIn('static ip_address=10.1.0.2/24',result)
        self.assertEqual(result.count('nohook wpa_supplicant'),1)
        self.assertEqual(path.with_name('dhcpcd.conf.before-flexclock-hotspot').read_text(),original)
        net.prepare_hotspot_config(path)
        self.assertEqual(path.read_text(),result)
        path.write_text('interface wlan0\nstatic ip_address=10.0.0.4/24\n')
        with self.assertRaises(ValueError): net.prepare_hotspot_config(path)
        self.assertEqual(path.read_text(),'interface wlan0\nstatic ip_address=10.0.0.4/24\n')

    def test_successful_idle_recovery_removes_hotspot_state(self):
        net.publish_hotspot('hotspot','FlexClock-ABCDEF')
        monitor = net.HotspotMonitor()
        monitor.next_retry = 0
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'station_status',return_value={'mode':'AP','wpa_state':'COMPLETED'}), patch.object(net,'ipv4_addresses',return_value=[{'local':net.HOTSPOT_IP}]), patch.object(net,'active',return_value=True), patch.object(net,'run',return_value=''), patch.object(net,'wpa',return_value='header\n0\tSaved\tany\t[DISABLED]\n1\tFlexClock\tany\t[CURRENT]'), patch.object(net,'hotspot_stop'), patch.object(net,'hotspot_start') as start, patch.object(net,'station_ready',return_value=True):
            monitor.tick()
        self.assertFalse(net.HOTSPOT_STATE.exists())
        start.assert_not_called()

    def test_ethernet_subnet_conflict_preserves_access(self):
        with patch.object(net,'require_hotspot_stack'), patch.object(net,'hotspot_ssid',return_value='FlexClock-ABCDEF'), patch.object(net,'ipv4_addresses',return_value=[{'local':'192.168.4.20','prefixlen':24}]), patch.object(net,'run') as run, patch.object(net,'wpa') as wpa:
            with self.assertRaisesRegex(ValueError,'conflicts with Ethernet'):
                net.hotspot_start()
            run.assert_not_called()
            wpa.assert_not_called()
            self.assertFalse(net.HOTSPOT_STATE.exists())
