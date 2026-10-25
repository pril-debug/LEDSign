#!/usr/bin/env python3
"""Build the single GitHub Release asset from the reviewed controller/web pair."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import shutil

ROOT = Path(__file__).resolve().parents[1]
APP_FILES = ('VERSION', 'FlexClock-TOD.lss', 'FlexClock.lss', 'lynx-logo.png', 'boot', 'modes', 'scripts', 'display_layout.py', 'setup.txt', 'setup_ledsign.sh', 'AGENTS.md')
EXCLUDE = shutil.ignore_patterns('__pycache__', '*.pyc', '.git', '.gitignore', 'config', 'venv', 'node_modules')


def copy_source(root, destination):
    destination.mkdir(parents=True)
    for name in APP_FILES:
        path = root/name
        if path.is_dir():
            shutil.copytree(path, destination/name, ignore=EXCLUDE)
        else:
            shutil.copy2(path, destination/name)
    shutil.copytree(root/'web', destination/'web', ignore=EXCLUDE)


def copy_tracked(root, destination):
    destination.mkdir(parents=True)
    for repo, prefix in ((root, ''), (root/'web', 'web')):
        names = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z']).decode().split('\0')
        for name in filter(None, names):
            path = Path(name)
            if not prefix and path.parts[0] not in APP_FILES:
                continue
            if any(part in ('config', 'venv', '.git', '__pycache__') for part in path.parts):
                continue
            source = repo/path
            if not source.is_file() or source.is_symlink():
                raise ValueError('Release files must be regular tracked files.')
            target = destination/prefix/path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='Release tag, such as 1.42')
    parser.add_argument('--output', type=Path, default=Path('/tmp/ledsign-release.tar.gz'))
    args = parser.parse_args()
    if not re.fullmatch(r'v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?', args.version):
        parser.error('Use a versioned release tag.')
    if (ROOT/'VERSION').read_text().strip() != args.version:
        parser.error('VERSION must match the release tag.')
    # Never publish local credentials or unreviewed working-tree changes.
    for path in (ROOT, ROOT/'web'):
        if subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain']).strip():
            parser.error(f'Commit the reviewed changes in {path} before packaging.')
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        copy_tracked(ROOT, stage/'app')
        # Explicit dependency contract; driver binary is retained from the Pi.
        shutil.copyfile(ROOT/'web/requirements.txt', stage/'app/requirements-update.txt')
        manifest = dict(format=1, version=args.version,
                        controller_commit=subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                        website_commit=subprocess.check_output(['git', '-C', str(ROOT/'web'), 'rev-parse', 'HEAD'], text=True).strip())
        (stage/'manifest.json').write_text(json.dumps(manifest, indent=2))
        with tarfile.open(args.output, 'w:gz') as archive:
            archive.add(stage/'manifest.json', arcname='manifest.json')
            archive.add(stage/'app', arcname='app')
    print(args.output)


if __name__ == '__main__':
    main()
