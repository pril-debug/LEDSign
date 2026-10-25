#!/usr/bin/python3
"""Narrow network operations. Invoked via sudo, JSON on stdin; no shell expansion."""
import ipaddress
import fcntl
import json
import os
import re
import subprocess
import sys
import socket
import tempfile
from contextlib import contextmanager
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
    unique = {}
    for ap in sorted(aps, key=lambda a: a['signal'] if a['signal'] is not None else -200, reverse=True):
        unique.setdefault((ap['ssid'],ap['security']),ap)
    return list(unique.values())


def wpa(*args):
    try:
        output = run(['wpa_cli', '-i', 'wlan0', *args])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError('Wi-Fi is not ready.')
    if 'FAIL' in output or 'UNKNOWN COMMAND' in output:
        raise ValueError('Wi-Fi is not ready. Try again.')
    if args and args[0] == 'save_config':
        for filename in ('wpa_supplicant-wlan0.conf', 'wpa_supplicant.conf'):
            path = Path('/etc/wpa_supplicant') / filename
            if path.exists():
                os.chmod(path, 0o600)
    return output


@contextmanager
def wifi_events():
    """Listen for authentication failures without reading or exposing saved keys."""
    with tempfile.TemporaryDirectory(prefix='flexclock-wpa-', dir='/run') as directory:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            client.bind(directory + '/events')
            client.settimeout(2)
            client.connect('/run/wpa_supplicant/wlan0')
            client.send(b'ATTACH')
            if client.recv(4096).strip() != b'OK':
                raise ValueError('Wi-Fi is not ready.')
            client.setblocking(False)
            yield client
        except OSError:
            raise ValueError('Wi-Fi is not ready.')
        finally:
            client.close()


def authentication_failed(events):
    while True:
        try:
            text = events.recv(8192).decode(errors='replace')
        except BlockingIOError:
            return False
        if 'WRONG_KEY' in text or 'AUTH_FAILED' in text:
            return True


def ipv4_addresses(iface):
    try:
        interfaces = json.loads(run(['ip', '-j', '-4', 'address', 'show', 'dev', iface]))
        return [a for item in interfaces for a in item.get('addr_info', []) if a.get('family') == 'inet']
    except ValueError:
        return []


def wait_address(iface, mode=None, address=None, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for item in ipv4_addresses(iface):
            if mode == 'static':
                expected = ipaddress.IPv4Interface(address)
                if item.get('local') != str(expected.ip) or item.get('prefixlen') != expected.network.prefixlen or item.get('dynamic'):
                    continue
            if mode == 'dhcp' and not item.get('dynamic'):
                continue
            return item['local']
        time.sleep(.5)
    return None


def wifi(data):
    require_stack()
    if wpa('ping') != 'PONG':
        raise ValueError('Wi-Fi is not ready.')
    if data.get('disconnect'):
        wpa('disable_network', 'all')
        wpa('save_config')
        wpa('disconnect')
        if 'wpa_state=COMPLETED' in wpa('status'):
            raise ValueError('Wi-Fi did not disconnect. Try again.')
        return {'message': 'Wi-Fi disconnected.', 'connected': False}
    ssid, psk, security = data.get('ssid', ''), data.get('psk', ''), data.get('security')
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid):
        raise ValueError('Enter a network name of up to 32 characters.')
    if security not in ('open', 'wpa2'):
        raise ValueError('Choose the network security type.')
    if security == 'wpa2' and not (isinstance(psk, str) and 8 <= len(psk) <= 63 and all(32 <= ord(c) <= 126 for c in psk)):
        raise ValueError('Enter a Wi-Fi password of 8–63 characters.')
    profiles = wpa('list_networks')
    previous = [line.split('\t')[0] for line in profiles.splitlines()[1:]
                if len(line.split('\t')) > 1 and line.split('\t')[1] == ssid and line.split('\t')[0].isdigit()]
    with wifi_events() as events:
        network = wpa('add_network')
        if not network.isdigit():
            raise ValueError('Unable to connect. Try again.')
        try:
            wpa('set_network', network, 'ssid', ssid.encode().hex())
            wpa('set_network', network, 'scan_ssid', '1')
            wpa('set_network', network, 'key_mgmt', 'NONE' if security == 'open' else 'WPA-PSK')
            if security != 'open':
                import hashlib
                key = hashlib.pbkdf2_hmac('sha1', psk.encode(), ssid.encode(), 4096, 32).hex()
                wpa('set_network', network, 'psk', key)
            wpa('select_network', network)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if authentication_failed(events):
                    raise ValueError('Incorrect Wi-Fi password.')
                state = dict(line.split('=', 1) for line in wpa('status').splitlines() if '=' in line)
                if state.get('wpa_state') == 'COMPLETED' and state.get('id') == network:
                    # Save only after the requested network actually associates.
                    for old in previous:
                        wpa('remove_network', old)
                    wpa('save_config')
                    break
                time.sleep(.5)
            else:
                raise ValueError('Unable to connect. Check the network name, password and signal.')
        except Exception:
            try:
                wpa('remove_network', network)
                wpa('reconfigure')
            except ValueError:
                pass
            raise
    ip = wait_address('wlan0')
    return {'message': 'Wi-Fi connected.' if ip else 'Connected, but no IP address was assigned.',
            'connected': True, 'ip': ip}


def set_address(data, iface):
    require_stack()
    if iface not in ('eth0', 'wlan0'):
        raise ValueError('Invalid network connection.')
    mode = data.get('mode')
    if mode not in ('static', 'dhcp'):
        raise ValueError('Invalid Ethernet mode.')
    block = f'# LEDSign {iface} BEGIN\ninterface {iface}\n'
    if iface == 'wlan0':
        block += 'metric 3000\n'
    if mode == 'static':
        address = ipaddress.IPv4Interface(data['address'])
        gw = ipaddress.IPv4Address(data['gateway'])
        dns = [str(ipaddress.IPv4Address(d)) for d in data['dns']]
        if gw not in address.network or address.ip in (address.network.network_address, address.network.broadcast_address) or not dns:
            raise ValueError('Invalid static network.')
        other = 'eth0' if iface == 'wlan0' else 'wlan0'
        if any(a.get('local') == str(address.ip) for a in ipv4_addresses(other)):
            raise ValueError('That IP address is already used by the other connection.')
        block += f'static ip_address={address}\nstatic routers={gw}\nstatic domain_name_servers={" ".join(dns)}\n'
    block += f'# LEDSign {iface} END\n'
    path = Path('/etc/dhcpcd.conf')
    text = path.read_text()
    # Avoid silently overriding device-specific eth0 definitions outside our block.
    clean = re.sub(rf'(?ms)^# LEDSign {iface} BEGIN\n.*?^# LEDSign {iface} END\n?', '', text)
    if re.search(rf'(?m)^\s*interface\s+{iface}\b|^\s*profile\b', clean):
        raise ValueError('Existing network settings need to be reviewed before they can be changed.')
    backup = Path('/etc/dhcpcd.conf.flexclock-backup')
    backup.write_text(text)
    temp = path.with_suffix('.flexclock-tmp')
    temp.write_text(clean.rstrip() + '\n\n' + block)
    os.chmod(temp, 0o644)
    os.replace(temp, path)
    run(['dhcpcd', '-n', iface])
    if iface == 'wlan0':
        ip = wait_address(iface, mode, data.get('address'))
        return {'message': 'Wi-Fi address settings applied.' if ip else 'Settings saved, but no matching IP address was assigned.',
                'ip': ip, 'applied': bool(ip)}
    return {'message': 'Ethernet settings saved. Reconnect at the new IP address if needed.', 'applied': True}


def ethernet(data):
    return set_address(data, 'eth0')


def wifi_address(data):
    if wpa('ping') != 'PONG':
        raise ValueError('Wi-Fi is not ready.')
    return set_address(data, 'wlan0')


def main():
    try:
        action = sys.argv[1]
        data = json.loads(sys.stdin.read(8192) or '{}')
        if action == 'scan':
            result = parse_scan(run(['iw', 'dev', 'wlan0', 'scan'], timeout=20))
        else:
            with open('/run/flexclock-network.lock', 'w') as lock:
                os.chmod('/run/flexclock-network.lock', 0o600)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('Another connection change is in progress. Try again shortly.')
                result = {'wifi': wifi, 'ethernet': ethernet, 'wifi_address': wifi_address}[action](data)
        print(json.dumps(result))
    except Exception as e:
        print(str(e) if isinstance(e, ValueError) else 'Network operation failed; inspect device logs.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
