#!/usr/bin/python3
"""Narrow controller-only apply helper. Never changes network services."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from display_layout import validate_layout

ACTIVE = Path(Path('/usr/local/libexec/flexclock/display-active-path').read_text().strip()) if Path('/usr/local/libexec/flexclock/display-active-path').exists() else Path(__file__).resolve().parents[1] / 'config/display-active.json'


def run(payload):
    layout = validate_layout(payload.get('layout'))
    if payload.get('action') not in ('apply', 'test'):
        raise ValueError('Invalid display action.')
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'sign-ui'], timeout=5).returncode:
        raise ValueError('The display controller must be running before applying settings.')
    previous = ACTIVE.read_bytes() if ACTIVE.exists() else None
    data = dict(layout=layout, test_until=time.time()+15 if payload['action'] == 'test' else 0)
    if payload['action'] == 'test':
        from display_layout import saved_layout
        data['restore'] = json.loads(previous)['layout'] if previous else saved_layout(json.loads(ACTIVE.with_name('settings.json').read_text()))
    tmp = ACTIVE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data)); os.chmod(tmp, 0o644); os.replace(tmp, ACTIVE)
    try:
        subprocess.run(['systemctl', 'restart', 'sign-ui'], check=True, timeout=20)
        time.sleep(2)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'sign-ui'], check=True, timeout=5)
        if payload['action'] == 'test':
            time.sleep(15)
            if previous is None:
                ACTIVE.unlink(missing_ok=True)
            else:
                tmp.write_bytes(previous); os.replace(tmp, ACTIVE)
            subprocess.run(['systemctl', 'restart', 'sign-ui'], check=True, timeout=20)
            time.sleep(2)
            subprocess.run(['systemctl', 'is-active', '--quiet', 'sign-ui'], check=True, timeout=5)
    except Exception:
        if previous is None:
            ACTIVE.unlink(missing_ok=True)
        else:
            tmp.write_bytes(previous); os.replace(tmp, ACTIVE)
        subprocess.run(['systemctl', 'restart', 'sign-ui'], timeout=20)
        raise ValueError('The display controller could not apply the mapping. Previous settings restored.')


if __name__ == '__main__':
    try:
        lock = open('/run/lock/flexclock-display.lock', 'w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another display operation is in progress. Try again shortly.')
        run(json.loads(sys.stdin.read(65537)))
        print(json.dumps({'ok': True}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else 'Display settings could not be applied.'}))
        sys.exit(1)
