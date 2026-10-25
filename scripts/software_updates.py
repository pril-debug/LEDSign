#!/usr/bin/python3
"""Root-owned, manual release updater. No OS provisioning or network changes."""
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

BASE = Path('/opt/flexclock')
STATE = Path('/var/lib/flexclock-updates')
HELPERS = Path('/usr/local/libexec/flexclock')
SETTINGS = Path('/etc/flexclock-updates.json')
API = 'https://api.github.com/repos/pril-debug/LEDSign/releases'
ASSET = 'ledsign-release.tar.gz'
TAG = re.compile(r'v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\Z')
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_UNPACK = 512 * 1024 * 1024
BUSY = {'queued', 'downloading', 'preparing', 'installing', 'verifying', 'restoring'}


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w') as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.chmod(0o644)
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def status(phase, message):
    previous = read(STATE/'status.json', {})
    previous.update(phase=phase, message=message, updated=datetime.now(timezone.utc).isoformat())
    atomic(STATE/'status.json', previous)


def command(args, timeout=60):
    # Output is available to developers in the service journal only.
    subprocess.run(args, check=True, timeout=timeout)


def fetch_json(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'FlexClock-Updater'})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read(2*1024*1024+1)
    if len(data) > 2*1024*1024:
        raise ValueError('Release information is too large.')
    return json.loads(data)


def release_info(item):
    tag = item.get('tag_name', '')
    if not TAG.fullmatch(tag) or item.get('draft') or item.get('prerelease'):
        return None
    asset = next((a for a in item.get('assets', []) if a.get('name') == ASSET), None)
    return dict(version=tag, notes=(item.get('body') or '')[:30000],
                published=item.get('published_at'), installable=bool(asset and
                re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or '')))


def check_releases():
    items = fetch_json(API+'?per_page=50')
    releases = [info for item in items if (info := release_info(item))]
    atomic(STATE/'releases.json', dict(releases=releases, checked=datetime.now(timezone.utc).isoformat()))
    return releases


def download(tag, destination):
    if not TAG.fullmatch(tag):
        raise ValueError('Choose a versioned release.')
    from urllib.parse import quote
    release = fetch_json(API+'/tags/'+quote(tag, safe=''))
    info = release_info(release)
    if not info or info['version'] != tag or not info['installable']:
        raise ValueError('This release has no verified installation package.')
    asset = next(a for a in release['assets'] if a.get('name') == ASSET)
    expected = f'https://github.com/pril-debug/LEDSign/releases/download/{quote(tag, safe="")}/{ASSET}'
    if asset.get('browser_download_url') != expected:
        raise ValueError('The release package address is invalid.')
    digest = hashlib.sha256()
    count = 0
    with urllib.request.urlopen(urllib.request.Request(expected, headers={'User-Agent': 'FlexClock-Updater'}), timeout=30) as response, destination.open('wb') as output:
        while chunk := response.read(1024*1024):
            count += len(chunk)
            if count > MAX_DOWNLOAD:
                raise ValueError('The release package is too large.')
            digest.update(chunk)
            output.write(chunk)
    if 'sha256:'+digest.hexdigest() != asset['digest']:
        raise ValueError('The release package could not be verified.')


def unpack(archive, destination, tag):
    destination.mkdir()
    total = 0
    names = set()
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts or
                    path.parts[0] not in ('app', 'manifest.json') or
                    not (member.isdir() or member.isfile()) or member.name in names):
                raise ValueError('The release package contains invalid files.')
            names.add(member.name)
            # No release may supply device data, executable environments, or Git state.
            if any(part in ('config', 'venv', '.git', '__pycache__') for part in path.parts):
                raise ValueError('The release package contains device-specific files.')
            total += member.size
            if total > MAX_UNPACK or len(names) > 20000:
                raise ValueError('The release package is too large.')
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as src, target.open('wb') as out:
                    shutil.copyfileobj(src, out)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    manifest = read(destination/'manifest.json', {})
    if manifest.get('format') != 1 or manifest.get('version') != tag:
        raise ValueError('The package version does not match the release.')
    app = destination/'app'
    required = ('boot/splash.py', 'modes/matrix_utils.py', 'web/app.py', 'web/updates.py',
                'web/templates/updates.html', 'scripts/software_updates.py', 'display_layout.py', 'requirements-update.txt')
    if any(not (app/name).is_file() for name in required):
        raise ValueError('The release package is incomplete.')
    (app/'VERSION').write_text(tag+'\n')
    return app


def version(target):
    return (target/'VERSION').read_text().strip()


def switch(target):
    link = BASE/'current.new'
    link.unlink(missing_ok=True)
    link.symlink_to(target, target_is_directory=True)
    os.replace(link, BASE/'current')
    fd = os.open(BASE, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_helpers(target):
    for filename in ('web_network.py', 'web_hostname.py', 'web_display.py'):
        source = target/'scripts'/filename
        if not source.is_file():
            raise ValueError('The release package is incomplete.')
        install_file(source, HELPERS/filename, 0o755)
    install_file(target/'display_layout.py', HELPERS/'display_layout.py', 0o644)
    if (HELPERS/'__pycache__').exists():
        shutil.rmtree(HELPERS/'__pycache__')


def install_file(source, target, mode):
    tmp = target.with_suffix('.new')
    shutil.copyfile(source, tmp)
    tmp.chmod(mode)
    with tmp.open('rb') as stream:
        os.fsync(stream.fileno())
    os.replace(tmp, target)
    fd = os.open(target.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def restart():
    command(['systemctl', 'restart', 'sign-ui', 'sign-web'], timeout=60)


def verify(target):
    # A changing PID catches restart loops even if is-active happens to succeed.
    deadline = time.monotonic()+45
    stable = None
    since = None
    while time.monotonic() < deadline:
        try:
            pids = []
            for service in ('sign-ui', 'sign-web'):
                command(['systemctl', 'is-active', '--quiet', service], timeout=5)
                pid = subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value', service], timeout=5).strip()
                if pid == b'0':
                    raise ValueError('Service has no process')
                pids.append(pid)
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3) as response:
                health = json.load(response)
            if health.get('status') != 'ok' or health.get('version') != version(target):
                raise ValueError('Application health check failed')
            if pids != stable:
                stable, since = pids, time.monotonic()
            elif time.monotonic()-since >= 5:
                return
        except Exception:
            stable, since = None, None
        time.sleep(1)
    raise ValueError('The application did not pass its startup checks.')


def history(action, old, new, success, message):
    rows = read(STATE/'history.json', [])
    rows.append(dict(date=datetime.now(timezone.utc).isoformat(), previous=old, version=new,
                     action=action, success=success, message=message))
    atomic(STATE/'history.json', rows)


def cleanup():
    installed = read(STATE/'installed.json', {})
    keep = {Path(installed[key]).parent for key in ('current', 'previous') if installed.get(key)}
    for directory in (BASE/'releases').iterdir():
        if directory.is_dir() and directory not in keep:
            try:
                shutil.rmtree(directory)
            except OSError:
                logging.exception('Obsolete release cleanup failed')
    (STATE/'download.tar.gz').unlink(missing_ok=True)


def restore(journal):
    target = Path(journal['old'])
    status('restoring', 'Restoring the previous application…')
    switch(target)
    sync_helpers(target)
    restart()
    verify(target)
    install_file(target/'scripts/software_updates.py', HELPERS/'software_updates.py', 0o755)


def recover():
    journal = read(STATE/'transaction.json')
    if not journal:
        return
    try:
        restore(journal)
        atomic(STATE/'installed.json', journal['installed'])
        message = 'Update interrupted. Previous application restored.'
        status('failed', message)
        history(journal['action'], journal['old_version'], journal['new_version'], False, message)
        (STATE/'transaction.json').unlink()
    except Exception:
        logging.exception('Recovery failed')
        status('error', 'Recovery failed. Ask your installer to restore the application.')
        raise


def prepare(tag, current):
    workspace = BASE/'releases'/uuid.uuid4().hex
    archive = STATE/'download.tar.gz'
    status('downloading', 'Downloading the selected release…')
    download(tag, archive)
    status('preparing', 'Preparing application files and dependencies…')
    try:
        target = unpack(archive, workspace, tag)
        config = read(SETTINGS)
        (target/'config').symlink_to(config['config'], target_is_directory=True)
        # Preserve the Pi-specific GPIO driver and all baseline dependencies.
        shutil.copytree(current/'venv', target/'venv', symlinks=True)
        command([str(target/'venv/bin/python'), '-m', 'pip', 'install', '--disable-pip-version-check',
                 '-r', str(target/'requirements-update.txt')], timeout=600)
        command([str(target/'venv/bin/python'), '-m', 'pip', 'check'], timeout=60)
        command(['/usr/bin/python3', '-m', 'py_compile', str(target/'scripts/software_updates.py')], timeout=30)
        command(['/usr/bin/python3', str(target/'scripts/software_updates.py'), 'self-check'], timeout=30)
        env = dict(os.environ, PYTHONPATH=str(target/'web')+os.pathsep+str(target))
        subprocess.run([str(target/'venv/bin/python'), '-c', 'import flask, waitress, PIL, rgbmatrix; import app; import modes.menu'],
                       cwd=target, env=env, check=True, timeout=30)
        return target
    except Exception:
        if workspace.exists():
            shutil.rmtree(workspace)
        raise
    finally:
        archive.unlink(missing_ok=True)


def transact(request):
    installed = read(STATE/'installed.json')
    current = Path(installed['current'])
    action = request['action']
    old = version(current)
    new = request.get('version') or installed.get('previous_version')
    journal = None
    target = None
    try:
        if action == 'install':
            target = prepare(new, current)
        else:
            if not installed.get('previous'):
                raise ValueError('No previous version is available.')
            target = Path(installed['previous'])
            new = version(target)
        journal = dict(old=str(current), new=str(target), installed=installed, action=action,
                       old_version=old, new_version=new)
        atomic(STATE/'transaction.json', journal)
        status('installing', 'Installing application software…')
        switch(target)
        sync_helpers(target)
        restart()
        status('verifying', 'Checking the display controller and dashboard…')
        verify(target)
        # The running worker retains its code; the next job uses the new updater.
        install_file(target/'scripts/software_updates.py', HELPERS/'software_updates.py', 0o755)
        atomic(STATE/'installed.json', dict(current=str(target), version=new, previous=str(current), previous_version=old))
        message = 'Update installed.' if action == 'install' else 'Previous version restored.'
        history(action, old, new, True, message)
        (STATE/'transaction.json').unlink()
        status('success', message)
        cleanup()
    except Exception:
        logging.exception('Software operation failed')
        message = 'Installation failed. The current application was kept.'
        if journal:
            try:
                restore(journal)
                atomic(STATE/'installed.json', installed)
                (STATE/'transaction.json').unlink()
                message = 'Installation failed. Previous application restored.'
            except Exception:
                logging.exception('Automatic rollback failed')
                status('error', 'Recovery failed. Ask your installer to restore the application.')
                history(action, old, new, False, 'Automatic recovery failed.')
                return
        history(action, old, new, False, message)
        status('failed', message)
        if action == 'install' and target and target.parent.exists():
            shutil.rmtree(target.parent)


@contextlib.contextmanager
def lock(name):
    with (STATE/name).open('w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def submit(payload):
    if payload.get('action') not in ('install', 'rollback'):
        raise ValueError('Choose Install Update or Rollback.')
    if payload['action'] == 'install' and not TAG.fullmatch(payload.get('version', '')):
        raise ValueError('Choose a versioned release.')
    with lock('submit.lock'), lock('worker.lock'):
        service_state = subprocess.check_output(['systemctl', 'show', '--property=ActiveState', '--value',
                                                  'flexclock-update.service'], timeout=5).strip()
        if service_state in (b'activating', b'active', b'deactivating'):
            raise ValueError('An update is already in progress.')
        if read(STATE/'status.json', {}).get('phase') in BUSY or (STATE/'transaction.json').exists():
            raise ValueError('An update is already in progress.')
        if payload['action'] == 'rollback' and not read(STATE/'installed.json', {}).get('previous'):
            raise ValueError('No previous version is available.')
        atomic(STATE/'request.json', dict(action=payload['action'], version=payload.get('version')))
        status('queued', 'Preparing the software operation…')
        try:
            command(['systemctl', 'start', '--no-block', 'flexclock-update.service'], timeout=10)
        except Exception:
            status('failed', 'The software operation could not start.')
            raise


def main():
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ['self-check']:
        return
    elif sys.argv[1:] == ['worker']:
        with lock('worker.lock'):
            request = read(STATE/'request.json')
            if not request or read(STATE/'status.json', {}).get('phase') != 'queued':
                logging.info('No queued software operation.')
                return
            transact(request)
    elif sys.argv[1:] == ['recover']:
        with lock('worker.lock'):
            recover()
            if read(STATE/'status.json', {}).get('phase') in BUSY:
                request = read(STATE/'request.json', {})
                installed = read(STATE/'installed.json', {})
                history(request.get('action', 'install'), installed.get('version'), request.get('version'), False,
                        'Software operation interrupted. Current application kept.')
                status('failed', 'The software operation was interrupted. Current application kept.')
            if not (STATE/'transaction.json').exists():
                cleanup()
    elif not sys.argv[1:]:
        try:
            payload = json.loads(sys.stdin.read(4097))
            submit(payload)
            print(json.dumps(dict(ok=True)))
        except Exception as error:
            logging.exception('Update request failed')
            print(json.dumps(dict(ok=False, message=str(error) if isinstance(error, ValueError)
                                  else 'The software operation could not start.')))
            sys.exit(1)
    else:
        sys.exit(2)


if __name__ == '__main__':
    main()
