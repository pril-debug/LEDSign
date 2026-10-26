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


def validate_wifi(data):
    ssid, psk, security = data.get('ssid', ''), data.get('psk', ''), data.get('security')
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid):
        raise ValueError('Enter a network name of up to 32 characters.')
    if security not in ('open', 'wpa2'):
        raise ValueError('Choose the network security type.')
    if security == 'wpa2' and not (isinstance(psk, str) and 8 <= len(psk) <= 63 and all(32 <= ord(c) <= 126 for c in psk)):
        raise ValueError('Enter a Wi-Fi password of 8–63 characters.')


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
    validate_wifi(data)
    ssid, psk, security = data.get('ssid', ''), data.get('psk', ''), data.get('security')
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
            run(['ip', '-4', 'address', 'flush', 'dev', 'wlan0', 'scope', 'global'])
            wpa('select_network', network)
            run(['dhcpcd', '-n', 'wlan0'])
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if authentication_failed(events):
                    raise ValueError('Incorrect Wi-Fi password.')
                state = dict(line.split('=', 1) for line in wpa('status').splitlines() if '=' in line)
                if state.get('wpa_state') == 'COMPLETED' and state.get('id') == network:
                    break
                time.sleep(.5)
            else:
                raise ValueError('Unable to connect. Check the network name, password and signal.')
            ip = wait_address('wlan0')
            if HOTSPOT_ENABLED.exists() and (not ip or ipaddress.IPv4Address(ip).is_link_local
                                              or ip == HOTSPOT_IP):
                raise ValueError('Unable to connect. No Wi-Fi address was assigned.')
            # Keep the previous saved credentials until fallback-enabled devices
            # have usable connectivity, including successful address assignment.
            for old in previous:
                wpa('remove_network', old)
            wpa('save_config')
        except Exception:
            try:
                wpa('remove_network', network)
                wpa('reconfigure')
            except ValueError:
                pass
            raise
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
        if HOTSPOT_ENABLED.exists():
            block += 'nohook wpa_supplicant\n'
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
    if HOTSPOT_ENABLED.exists() and HOTSPOT_STATE.exists():
        raise ValueError('Wi-Fi is not ready. Connect to a network before changing its address.')
    if wpa('ping') != 'PONG':
        raise ValueError('Wi-Fi is not ready.')
    return set_address(data, 'wlan0')


# The AP is a runtime-only supplicant profile: never save it over station settings.
HOTSPOT_STATE = Path('/run/flexclock-hotspot.json')
HOTSPOT_ENABLED = Path('/etc/flexclock-hotspot-enabled')
HOTSPOT_IP = '192.168.4.1'
WIFI_PAUSED = Path('/var/lib/flexclock-network/paused')


def prepare_hotspot_config(path):
    # The interface unit owns the supplicant. A dhcpcd STOP hook must not kill it.
    text = path.read_text()
    block = re.search(r'(?ms)^# LEDSign wlan0 BEGIN\n.*?^# LEDSign wlan0 END\n?', text)
    clean = text[:block.start()] + text[block.end():] if block else text
    if re.search(r'(?m)^\s*interface\s+wlan0\b|^\s*profile\b', clean):
        raise ValueError('Review unmanaged wlan0/profile settings before installing fallback.')
    replacement = block[0] if block else '# LEDSign wlan0 BEGIN\ninterface wlan0\nmetric 3000\n# LEDSign wlan0 END\n'
    if 'nohook wpa_supplicant' not in replacement:
        replacement = replacement.replace('# LEDSign wlan0 END', 'nohook wpa_supplicant\n# LEDSign wlan0 END')
        path.with_name(path.name + '.before-flexclock-hotspot').write_text(text)
        temp = path.with_suffix('.hotspot-tmp')
        temp.write_text(text[:block.start()] + replacement + text[block.end():] if block else text.rstrip()+'\n\n'+replacement)
        temp.chmod(0o644)
        temp.replace(path)


def hotspot_ssid():
    """Use hardware identity, never an OS-generated ID or a Wi-Fi MAC."""
    try:
        serial = Path('/proc/device-tree/serial-number').read_bytes().decode('ascii').strip('\x00\n ')
    except (OSError, UnicodeError):
        match = re.search(r'(?mi)^Serial\s*:\s*([0-9a-f]+)\s*$', Path('/proc/cpuinfo').read_text())
        serial = match[1] if match else ''
    if not re.fullmatch(r'[0-9a-fA-F]{6,16}', serial) or int(serial, 16) == 0:
        raise ValueError('Raspberry Pi hardware serial is unavailable.')
    return 'FlexClock-' + serial[-6:].upper()


def station_status():
    return dict(line.split('=', 1) for line in wpa('status').splitlines() if '=' in line)


def station_ready():
    state = station_status()
    return (state.get('wpa_state') == 'COMPLETED' and state.get('mode') != 'AP'
            and any(not ipaddress.IPv4Address(a['local']).is_link_local
                    and a['local'] != HOTSPOT_IP for a in ipv4_addresses('wlan0')))


def publish_hotspot(mode, ssid=None):
    temp = HOTSPOT_STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(mode=mode, ssid=ssid, url='http://' + HOTSPOT_IP)))
    temp.chmod(0o644)
    os.replace(temp, HOTSPOT_STATE)


def require_hotspot_stack():
    # dhcpcd may exit after releasing its last managed interface. Station
    # recovery starts it again; an already established AP needs no DHCP client.
    if active('NetworkManager') or (not active('dhcpcd') and not HOTSPOT_STATE.exists()):
        raise ValueError('The configured network stack is unavailable.')


def hotspot_start():
    require_hotspot_stack()
    ssid = hotspot_ssid()
    if any(ipaddress.IPv4Interface(f"{a['local']}/{a.get('prefixlen', 32)}").network.overlaps(
            ipaddress.IPv4Interface(HOTSPOT_IP + '/24').network) for a in ipv4_addresses('eth0')):
        raise ValueError('The hotspot address conflicts with Ethernet. Existing access was preserved.')
    if 'AP' not in wpa('get_capability', 'modes').split():
        raise ValueError('This Wi-Fi adapter cannot create a hotspot.')
    publish_hotspot('starting', ssid)
    # Release only wlan0. Do not restart the shared daemon or touch Ethernet.
    run(['systemctl', 'stop', 'flexclock-hotspot-dhcp.service'])
    wpa('reconfigure')
    if active('dhcpcd'):
        run(['dhcpcd', '-k', 'wlan0'])
    network = wpa('add_network')
    if not network.isdigit():
        raise ValueError('Unable to create hotspot.')
    for key, value in [('ssid', ssid.encode().hex()), ('mode', '2'),
                       ('key_mgmt', 'NONE'), ('frequency', '2412')]:
        wpa('set_network', network, key, value)
    wpa('select_network', network)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = station_status()
        if state.get('mode') == 'AP' and state.get('wpa_state') == 'COMPLETED':
            break
        time.sleep(.5)
    else:
        raise ValueError('Unable to create hotspot.')
    run(['ip', '-4', 'address', 'flush', 'dev', 'wlan0', 'scope', 'global'])
    run(['ip', 'address', 'add', HOTSPOT_IP + '/24', 'dev', 'wlan0'])
    run(['systemctl', 'start', 'flexclock-hotspot-dhcp.service'])
    publish_hotspot('hotspot', ssid)


def hotspot_stop():
    publish_hotspot('connecting')
    run(['systemctl', 'stop', 'flexclock-hotspot-dhcp.service'])
    # Reload discards the unsaved AP profile and restores saved station profiles.
    wpa('reconfigure')
    run(['ip', '-4', 'address', 'flush', 'dev', 'wlan0', 'scope', 'global'])
    run(['systemctl', 'start', 'dhcpcd'])
    run(['dhcpcd', '-n', 'wlan0'])


def hotspot_active():
    return HOTSPOT_ENABLED.exists() and (HOTSPOT_STATE.exists() or station_status().get('mode') == 'AP')


def wifi_with_fallback(data):
    if not HOTSPOT_ENABLED.exists():
        return wifi(data)
    # Validate before removing the AP; wifi also performs authoritative validation.
    if not data.get('disconnect'):
        validate_wifi(data)
    require_hotspot_stack()
    try:
        if hotspot_active():
            hotspot_stop()
        result = wifi(data)
        if data.get('disconnect'):
            WIFI_PAUSED.touch(mode=0o600)
            hotspot_start()
        elif not result.get('ip') or ipaddress.IPv4Address(result['ip']).is_link_local:
            raise ValueError('Unable to connect. No Wi-Fi address was assigned.')
        else:
            WIFI_PAUSED.unlink(missing_ok=True)
            HOTSPOT_STATE.unlink(missing_ok=True)
        return result
    except Exception:
        try:
            hotspot_start()
        except Exception:
            # The monitor retries partial transitions after this lock is released.
            print('Hotspot restoration failed; monitor will retry.', file=sys.stderr)
        raise


@contextmanager
def network_lock():
    with open('/run/flexclock-network.lock', 'w') as lock:
        os.chmod('/run/flexclock-network.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class HotspotMonitor:
    def __init__(self):
        self.lost_since = None
        self.next_retry = time.monotonic() + 300

    def tick(self):
        require_hotspot_stack()
        now = time.monotonic()
        state = station_status()
        if state.get('mode') == 'AP' or HOTSPOT_STATE.exists():
            healthy = (state.get('mode') == 'AP' and state.get('wpa_state') == 'COMPLETED'
                       and any(a['local'] == HOTSPOT_IP for a in ipv4_addresses('wlan0'))
                       and active('flexclock-hotspot-dhcp.service'))
            if not healthy:
                hotspot_start()
                self.next_retry = time.monotonic() + 300
                return
            # Never kick associated configuration clients off the hotspot.
            if WIFI_PAUSED.exists() or now < self.next_retry:
                return
            self.next_retry = now + 300
            if run(['iw', 'dev', 'wlan0', 'station', 'dump']):
                return
            # No configured/enabled stations: keep the AP indefinitely.
            profiles = wpa('list_networks').splitlines()[1:]
            if not any('[DISABLED]' in row for row in profiles if '[CURRENT]' not in row):
                return
            hotspot_stop()
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if station_ready():
                        HOTSPOT_STATE.unlink(missing_ok=True)
                        self.lost_since = None
                        return
                    time.sleep(1)
            finally:
                if not station_ready():
                    hotspot_start()
            return
        if station_ready():
            self.lost_since = None
        elif self.lost_since is None:
            self.lost_since = now
        elif now - self.lost_since >= 30:
            hotspot_start()
            self.next_retry = time.monotonic() + 300


def monitor():
    monitor = HotspotMonitor()
    while True:
        try:
            with network_lock():
                monitor.tick()
        except BlockingIOError:
            pass  # The authenticated web operation owns this transition.
        except Exception:
            import logging
            logging.exception('Hotspot monitor operation failed')
        time.sleep(2)


def main():
    try:
        action = sys.argv[1]
        if action == 'monitor':
            monitor()
            return
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
                result = {'wifi': wifi_with_fallback, 'ethernet': ethernet, 'wifi_address': wifi_address}[action](data)
        print(json.dumps(result))
    except Exception as e:
        print(str(e) if isinstance(e, ValueError) else 'Network operation failed; inspect device logs.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
