import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import tarfile
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('updater_test', Path(__file__).resolve().parents[1]/'scripts/software_updates.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class Updates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.base, self.state, self.helpers = root/'app', root/'state', root/'helpers'
        self.config = root/'config'
        for directory in (self.base/'releases', self.state, self.helpers, self.config):
            directory.mkdir(parents=True)
        self.patches = [patch.object(u, 'BASE', self.base), patch.object(u, 'STATE', self.state),
                        patch.object(u, 'HELPERS', self.helpers), patch.object(u, 'SETTINGS', root/'settings.json')]
        for p in self.patches:
            p.start()
        for name in ('settings.json', '.web-secret', 'customer_logo.png', 'display-active.json'):
            (self.config/name).write_bytes(('private '+name).encode())
        self.original = {p.name:p.read_bytes() for p in self.config.iterdir()}
        self.old = self.release('old', 'v1.0.0')
        self.new = self.release('new', 'v2.0.0')
        u.switch(self.old)
        u.atomic(self.state/'installed.json', dict(current=str(self.old), version='v1.0.0', previous=None))
        u.atomic(u.SETTINGS, dict(config=str(self.config), user='admin'))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def release(self, name, version):
        root = self.base/'releases'/name/'app'
        (root/'scripts').mkdir(parents=True)
        (root/'VERSION').write_text(version)
        (root/'config').symlink_to(self.config)
        for file in ('web_network.py', 'web_hostname.py', 'web_display.py', 'software_updates.py'):
            (root/'scripts'/file).write_text(version)
        (root/'display_layout.py').write_text(version)
        (root/'venv').mkdir()
        (root/'venv/dependency').write_text(version)
        return root

    def preserved(self):
        self.assertEqual({p.name:p.read_bytes() for p in self.config.iterdir()}, self.original)

    def test_install_and_offline_rollback_keep_dependencies_and_updater(self):
        with patch.object(u, 'prepare', return_value=self.new), patch.object(u, 'restart') as restart, patch.object(u, 'verify'):
            u.transact(dict(action='install', version='v2.0.0'))
        self.assertEqual((self.base/'current').resolve(), self.new)
        self.assertEqual((self.helpers/'software_updates.py').read_text(), 'v2.0.0')
        self.assertEqual((self.old/'venv/dependency').read_text(), 'v1.0.0')
        self.assertEqual(u.read(self.state/'installed.json')['previous'], str(self.old))
        self.preserved()
        with patch.object(u, 'download', side_effect=AssertionError('offline')), patch.object(u, 'restart'), patch.object(u, 'verify'):
            u.transact(dict(action='rollback'))
        self.assertEqual((self.base/'current').resolve(), self.old)
        self.assertEqual((self.helpers/'software_updates.py').read_text(), 'v1.0.0')
        self.assertEqual(len(u.read(self.state/'history.json')), 2)
        self.assertTrue(all(row['success'] for row in u.read(self.state/'history.json')))
        self.preserved()

    def test_verification_failure_automatically_restores_working_version(self):
        with patch.object(u, 'prepare', return_value=self.new), patch.object(u, 'restart'), \
             patch.object(u, 'verify', side_effect=[ValueError('bad candidate'), None]):
            u.transact(dict(action='install', version='v2.0.0'))
        self.assertEqual((self.base/'current').resolve(), self.old)
        self.assertEqual(u.read(self.state/'installed.json')['version'], 'v1.0.0')
        self.assertEqual((self.helpers/'software_updates.py').read_text(), 'v1.0.0')
        self.assertFalse(u.read(self.state/'history.json')[0]['success'])
        self.assertFalse((self.state/'transaction.json').exists())
        self.preserved()

    def test_download_failure_keeps_current_without_restart(self):
        with patch.object(u, 'prepare', side_effect=OSError('offline')), patch.object(u, 'restart') as restart:
            u.transact(dict(action='install', version='v2.0.0'))
        restart.assert_not_called()
        self.assertEqual((self.base/'current').resolve(), self.old)
        self.assertEqual(u.read(self.state/'status.json')['phase'], 'failed')
        self.preserved()

    def test_interrupted_transaction_recovers_offline(self):
        installed = u.read(self.state/'installed.json')
        u.atomic(self.state/'transaction.json', dict(old=str(self.old), new=str(self.new), installed=installed,
                                                   action='install', old_version='v1.0.0', new_version='v2.0.0'))
        u.switch(self.new)
        with patch.object(u, 'restart'), patch.object(u, 'verify'), patch.object(u, 'download', side_effect=AssertionError):
            u.recover()
        self.assertEqual((self.base/'current').resolve(), self.old)
        self.assertFalse((self.state/'transaction.json').exists())
        self.preserved()

    def test_recovery_failure_is_reported_and_keeps_journal(self):
        with patch.object(u, 'prepare', return_value=self.new), patch.object(u, 'restart'), \
             patch.object(u, 'verify', side_effect=ValueError('failed')):
            u.transact(dict(action='install', version='v2.0.0'))
        self.assertEqual(u.read(self.state/'status.json')['phase'], 'error')
        self.assertTrue((self.state/'transaction.json').exists())

    def test_requests_are_manual_serialized_and_validated(self):
        with patch.object(u, 'command') as command, patch.object(u.subprocess, 'check_output', return_value=b'inactive'):
            with self.assertRaises(ValueError):
                u.submit(dict(action='install', version='main'))
            with self.assertRaises(ValueError):
                u.submit(dict(action='rollback'))
            u.submit(dict(action='install', version='v2.0.0'))
            command.assert_called_once()
            with self.assertRaises(ValueError):
                u.submit(dict(action='install', version='v2.0.0'))

    def archive(self, files):
        archive = self.state/'bundle.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for name, value in files.items():
                info = tarfile.TarInfo(name)
                if isinstance(value, bytes):
                    info.size = len(value)
                    output.addfile(info, io.BytesIO(value))
                else:
                    info.type = tarfile.SYMTYPE
                    info.linkname = value
                    output.addfile(info)
        return archive

    def test_archive_rejects_traversal_symlinks_and_configuration(self):
        for i, file in enumerate(('../secret', '/root/file', 'app/config/settings.json', 'app/web/.git/config', 'app/venv/file')):
            with self.assertRaises(ValueError):
                u.unpack(self.archive({file:b'bad'}), self.state/str(i), 'v2.0.0')
        with self.assertRaises(ValueError):
            u.unpack(self.archive({'app/link':'/etc'}), self.state/'link', 'v2.0.0')

    def test_complete_package_and_version_validation(self):
        files = {name:b'content' for name in (
            'app/boot/splash.py', 'app/modes/matrix_utils.py', 'app/web/app.py', 'app/web/updates.py',
            'app/web/templates/updates.html', 'app/scripts/software_updates.py', 'app/display_layout.py', 'app/requirements-update.txt')}
        files['manifest.json'] = json.dumps(dict(format=1,version='v2.0.0')).encode()
        target = u.unpack(self.archive(files), self.state/'valid', 'v2.0.0')
        self.assertEqual(u.version(target), 'v2.0.0')
        with self.assertRaises(ValueError):
            u.unpack(self.archive(files), self.state/'mismatch', 'v3.0.0')

    def test_download_checks_github_digest_and_source(self):
        payload = b'release package'
        asset = dict(name=u.ASSET, digest='sha256:'+hashlib.sha256(payload).hexdigest(),
                     browser_download_url='https://github.com/pril-debug/LEDSign/releases/download/v2.0.0/'+u.ASSET)
        release = dict(tag_name='v2.0.0', assets=[asset])
        with patch.object(u, 'fetch_json', return_value=release), patch.object(u.urllib.request, 'urlopen', return_value=io.BytesIO(payload)):
            u.download('v2.0.0', self.state/'download')
        self.assertEqual((self.state/'download').read_bytes(), payload)
        asset['digest'] = 'sha256:'+'0'*64
        with patch.object(u, 'fetch_json', return_value=release), patch.object(u.urllib.request, 'urlopen', return_value=io.BytesIO(payload)):
            with self.assertRaises(ValueError):
                u.download('v2.0.0', self.state/'download')

    def test_preparation_uses_separate_environment_and_keeps_config(self):
        package = self.state/'prepared'/'app'
        package.mkdir(parents=True)
        (package/'requirements-update.txt').write_text('Flask==3.1.2')
        with patch.object(u, 'download'), patch.object(u, 'unpack', return_value=package), \
             patch.object(u, 'command') as run, patch.object(u.subprocess, 'run'):
            target = u.prepare('v2.0.0', self.old)
        self.assertEqual((target/'venv/dependency').read_text(), 'v1.0.0')
        self.assertEqual((target/'config').resolve(), self.config)
        self.assertIn(str(target/'venv/bin/python'), run.call_args_list[0].args[0])
        self.preserved()

    def test_health_checks_both_services_and_exact_version(self):
        times = iter(range(100))
        with patch.object(u, 'command') as services, patch.object(u.subprocess, 'check_output', return_value=b'123\n'), \
             patch.object(u.time, 'monotonic', side_effect=lambda: next(times)), patch.object(u.time, 'sleep'), \
             patch.object(u.urllib.request, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(b'{"status":"ok","version":"v1.0.0"}')):
            u.verify(self.old)
        self.assertTrue(any('sign-ui' in call.args[0] for call in services.call_args_list))
        self.assertTrue(any('sign-web' in call.args[0] for call in services.call_args_list))
        times = iter(range(100))
        with patch.object(u, 'command'), patch.object(u.subprocess, 'check_output', return_value=b'123\n'), \
             patch.object(u.time, 'monotonic', side_effect=lambda: next(times)), patch.object(u.time, 'sleep'), \
             patch.object(u.urllib.request, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(b'{"status":"ok","version":"wrong"}')):
            with self.assertRaises(ValueError):
                u.verify(self.old)

    def test_only_application_services_restart(self):
        with patch.object(u, 'command') as command:
            u.restart()
        self.assertEqual(command.call_args.args[0], ['systemctl', 'restart', 'sign-ui', 'sign-web'])
