#!/usr/bin/python3
"""Narrow network operations. Invoked via sudo, JSON on stdin; no shell expansion."""
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def run(args, timeout=12):
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        # Never include command arguments (which may contain a PSK) in errors.
        raise ValueError(f'{args[0]} operation failed. Check interface availability, permissions and system logs.')
    return p.stdout.strip()


def active(service):
    return subprocess.run(['systemctl', 'is-active', '--quiet', service], capture_output=True).returncode == 0


def require_stack():
    if active('NetworkManager') or not active('dhcpcd'):
        raise ValueError('This installation requires active dhcpcd with wpa_supplicant. NetworkManager or unknown stacks are not modified.')


def parse_scan(text):
    aps = []
    for block in re.split(r'(?m)^BSS ', text)[1:]:
        ssid = re.search(r'(?m)^\s*SSID: (.*)$', block)
        signal = re.search(r'signal: (-?[\d.]+)', block)
        freq = re.search(r'freq: (\d+)', block)
        if ssid and ssid[1]:
            aps.append(dict(ssid=ssid[1], signal=float(signal[1]) if signal else None,
                            freq=int(freq[1]) if freq else None,
                            security='Secured' if re.search(r'RSN:|WPA:|Privacy', block) else 'Open'))
    return sorted(aps, key=lambda a: a['signal'] if a['signal'] is not None else -200, reverse=True)


def wpa(*args):
    output = run(['wpa_cli', '-i', 'wlan0', *args])
    if 'FAIL' in output or 'UNKNOWN COMMAND' in output:
        raise ValueError('wpa_supplicant rejected the operation. Check its control socket and configuration.')
    return output


def wifi(data):
    require_stack()
    if wpa('ping') != 'PONG':
        raise ValueError('wlan0 control socket is unavailable. Supervised Pi setup is required.')
    if data.get('disconnect'):
        # Disable persisted Wi-Fi profiles so autoconnect cannot immediately undo disconnect.
        wpa('disable_network', 'all')
        wpa('save_config')
        wpa('disconnect')
        return {'message': 'Wi-Fi profiles disabled and saved; disconnect requested. Ethernet was not modified.'}
    ssid, psk, security = data.get('ssid', ''), data.get('psk', ''), data.get('security')
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid):
        raise ValueError('SSID must be 1–32 UTF-8 bytes without control characters.')
    if security not in ('open', 'wpa2'):
        raise ValueError('Select open or WPA/WPA2 Personal. Enterprise and WPA3-only networks require OS setup.')
    if security == 'wpa2' and not (8 <= len(psk) <= 63 and all(32 <= ord(c) <= 126 for c in psk)):
        raise ValueError('WPA/WPA2 password must contain 8–63 printable ASCII characters.')
    # No file overwrites, network service restarts, Ethernet commands, or route flushes.
    network = wpa('add_network')
    if not network.isdigit():
        raise ValueError('Could not allocate a Wi-Fi profile.')
    try:
        wpa('set_network', network, 'ssid', ssid.encode().hex())
        wpa('set_network', network, 'scan_ssid', '1')
        wpa('set_network', network, 'key_mgmt', 'NONE' if security == 'open' else 'WPA-PSK')
        if security != 'open':
            import hashlib
            # Pass derived key rather than plaintext; no password on process command line.
            key = hashlib.pbkdf2_hmac('sha1', psk.encode(), ssid.encode(), 4096, 32).hex()
            wpa('set_network', network, 'psk', key)
        wpa('select_network', network)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = dict(line.split('=', 1) for line in wpa('status').splitlines() if '=' in line)
            if state.get('wpa_state') == 'COMPLETED' and state.get('id') == network:
                wpa('save_config')
                addr = run(['ip', '-4', '-o', 'addr', 'show', 'dev', 'wlan0'])
                return {'message': 'Wi-Fi associated; profile saved. ' + ('IPv4 observed: ' + addr if addr else 'IPv4 lease pending; refresh status. Reboot persistence still needs verification.')}
            time.sleep(1)
        raise ValueError('Wi-Fi association timed out. Check password, signal and security; no success was confirmed.')
    except Exception:
        wpa('remove_network', network)
        wpa('reconfigure')  # restore previously saved profiles, wlan0 only
        raise


def ethernet(data):
    require_stack()
    mode = data.get('mode')
    if mode not in ('static', 'dhcp'):
        raise ValueError('Invalid Ethernet mode.')
    block = '# LEDSign eth0 BEGIN\ninterface eth0\n'
    if mode == 'static':
        address = ipaddress.IPv4Interface(data['address'])
        gw = ipaddress.IPv4Address(data['gateway'])
        dns = [str(ipaddress.IPv4Address(d)) for d in data['dns']]
        if gw not in address.network or address.ip in (address.network.network_address, address.network.broadcast_address) or not dns:
            raise ValueError('Invalid static network.')
        block += f'static ip_address={address}\nstatic routers={gw}\nstatic domain_name_servers={" ".join(dns)}\n'
    block += '# LEDSign eth0 END\n'
    path = Path('/etc/dhcpcd.conf')
    text = path.read_text()
    # Avoid silently overriding device-specific eth0 definitions outside our block.
    clean = re.sub(r'(?ms)^# LEDSign eth0 BEGIN\n.*?^# LEDSign eth0 END\n?', '', text)
    if re.search(r'(?m)^\s*interface\s+eth0\b|^\s*profile\b', clean):
        raise ValueError('Existing unmanaged Ethernet configuration requires manual review.')
    backup = Path('/etc/dhcpcd.conf.flexclock-backup')
    backup.write_text(text)
    temp = path.with_suffix('.flexclock-tmp')
    temp.write_text(clean.rstrip() + '\n\n' + block)
    os.chmod(temp, 0o644)
    os.replace(temp, path)
    run(['dhcpcd', '-n', 'eth0'])
    return {'message': 'Ethernet configuration saved and eth0 rebind requested. Connectivity is not verified; reconnect at the configured address. Backup: /etc/dhcpcd.conf.flexclock-backup'}


def main():
    try:
        action = sys.argv[1]
        data = json.loads(sys.stdin.read(8192) or '{}')
        if action == 'scan':
            result = parse_scan(run(['iw', 'dev', 'wlan0', 'scan'], timeout=20))
        else:
            # Deliberately disabled until supervised commissioning; UI confirmation is also required.
            if not Path('/etc/flexclock-network-enabled').is_file():
                raise ValueError('Live network changes are locked pending supervised Pi verification. See setup.txt.')
            result = {'wifi': wifi, 'ethernet': ethernet}[action](data)
        print(json.dumps(result))
    except Exception as e:
        print(str(e) if isinstance(e, ValueError) else 'Network operation failed; inspect device logs.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
