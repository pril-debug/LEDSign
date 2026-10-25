#!/usr/bin/python3
"""Set a validated static/transient device hostname. JSON input, no shell."""
import json
import re
import subprocess
import sys


def validate_hostname(value):
    if isinstance(value, str):
        value = value.strip().lower()
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
        raise ValueError('Use letters, numbers and hyphens, with a letter or number at each end (up to 63 characters).')
    return value


def set_hostname(data):
    name = validate_hostname(data.get('hostname'))
    if data.get('confirm') is not True:
        raise ValueError('Confirm the device name change before saving.')
    try:
        result = subprocess.run(['/usr/bin/hostnamectl', 'set-hostname', name],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError('The system could not save the device name. Check the hostname service and try again.')
        observed = subprocess.run(['/usr/bin/hostnamectl', '--static'], capture_output=True, text=True, timeout=5)
        if observed.returncode or observed.stdout.strip() != name:
            raise ValueError('The device name change could not be verified. Refresh Settings before trying again.')
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('The hostname service did not respond. Refresh Settings and try again.')
    return {'message': 'Device name saved. Network discovery may take a moment to update.'}


def main():
    try:
        data = json.loads(sys.stdin.read(2048))
        if not isinstance(data, dict):
            raise ValueError('Invalid device name request.')
        print(json.dumps(set_hostname(data)))
    except (ValueError, TypeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
