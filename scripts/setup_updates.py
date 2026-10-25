#!/usr/bin/python3
"""Provision managed application versions without changing device configuration."""
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

from package_release import copy_source
import software_updates as update

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.getuid() != 0:
        sys.exit('Run this setup as root.')
    owner = pwd.getpwuid(ROOT.stat().st_uid)
    if owner.pw_uid == 0:
        sys.exit('The source checkout must belong to the device user.')
    update.BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
    update.STATE.mkdir(mode=0o755, parents=True, exist_ok=True)
    (update.BASE/'releases').mkdir(exist_ok=True)
    update.HELPERS.mkdir(parents=True, exist_ok=True)
    update.atomic(update.SETTINGS, dict(config=str(ROOT/'config'), user=owner.pw_name))
    if not (update.STATE/'installed.json').exists():
        # Keep the exact working files, including reviewed local changes, as the
        # initial rollback point. Do not reset either repository.
        target = update.BASE/'releases'/'initial'/'app'
        if target.parent.exists():
            shutil.rmtree(target.parent)
        copy_source(ROOT, target)
        shutil.copytree(ROOT/'venv', target/'venv', symlinks=True)
        (target/'config').symlink_to(ROOT/'config', target_is_directory=True)
        revision = subprocess.check_output(['git', '-C', str(ROOT), 'describe', '--always', '--dirty'], text=True).strip()
        version = (ROOT/'VERSION').read_text().strip() if (ROOT/'VERSION').exists() else 'Local build '+revision
        (target/'VERSION').write_text(version+'\n')
        update.switch(target)
        update.atomic(update.STATE/'installed.json', dict(current=str(target), version=version, previous=None, previous_version=None))
        update.status('idle', 'Ready. Check for releases when you choose.')
    # Root-owned updater is the only sudo entrypoint and cannot take worker/recover arguments via sudoers.
    update.install_file(ROOT/'scripts/software_updates.py', update.HELPERS/'software_updates.py', 0o755)
    units = Path('/etc/systemd/system')
    (units/'flexclock-update.service').write_text('''[Unit]
Description=FlexClock manual software update
After=network-online.target

[Service]
Type=oneshot
User=root
ExecStart=/usr/bin/python3 /usr/local/libexec/flexclock/software_updates.py worker
ExecStopPost=/usr/bin/python3 /usr/local/libexec/flexclock/software_updates.py recover
TimeoutStartSec=15min
TimeoutStopSec=180
KillMode=control-group
''')
    (units/'flexclock-update-recovery.service').write_text('''[Unit]
Description=Recover interrupted FlexClock software update
After=sign-ui.service sign-web.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/libexec/flexclock/software_updates.py recover
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
''')
    for name, body in {
        'sign-ui': f'''[Service]
User=root
WorkingDirectory={update.BASE}/current
Environment=LEDSIGN_DEVICE_USER={owner.pw_name}
ExecStart=
ExecStart={update.BASE}/current/venv/bin/python -m boot.splash
''',
        'sign-web': f'''[Service]
User={owner.pw_name}
WorkingDirectory={update.BASE}/current/web
ExecStart=
ExecStart={update.BASE}/current/venv/bin/python -m waitress --listen=127.0.0.1:8000 app:app
'''
    }.items():
        directory = units/(name+'.service.d')
        directory.mkdir(exist_ok=True)
        (directory/'software-updates.conf').write_text(body)
    sudoers = Path('/etc/sudoers.d/flexclock-updates')
    rule = f'{owner.pw_name} ALL=(root) NOPASSWD: /usr/local/libexec/flexclock/software_updates.py ""\n'
    candidate = update.STATE/'sudoers-check'
    candidate.write_text(rule)
    candidate.chmod(0o440)
    subprocess.run(['visudo', '-cf', str(candidate)], check=True)
    shutil.copyfile(candidate, sudoers)
    sudoers.chmod(0o440)
    candidate.unlink()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'flexclock-update-recovery.service'], check=True)
    print('Update system installed. Restart sign-ui and sign-web to use the managed application.')


if __name__ == '__main__':
    main()
