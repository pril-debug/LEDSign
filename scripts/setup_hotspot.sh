#!/usr/bin/env bash
# Install persistently; do not switch Wi-Fi or start fallback on the live device.
set -Eeuo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run this setup as root.' >&2; exit 1; }
if systemctl is-active --quiet NetworkManager || ! systemctl is-active --quiet dhcpcd; then
  echo 'Hotspot fallback requires the existing dhcpcd stack.' >&2
  exit 1
fi
command -v dnsmasq >/dev/null
source_dir="$(dirname "$(readlink -f "$0")")"
python3 - "$source_dir" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from web_network import hotspot_ssid, wpa
from pathlib import Path
hotspot_ssid()
if 'AP' not in wpa('get_capability', 'modes').split():
    raise SystemExit('This Wi-Fi adapter does not support access point mode.')
from web_network import prepare_hotspot_config
prepare_hotspot_config(Path('/etc/dhcpcd.conf'))
PY
install -d -o root -g root -m 0755 /usr/local/libexec/flexclock /var/lib/flexclock-network
install -o root -g root -m 0755 "$source_dir/web_network.py" /usr/local/libexec/flexclock/web_network.py
install -o root -g root -m 0644 "$source_dir/hotspot-dnsmasq.conf" /usr/local/libexec/flexclock/hotspot-dnsmasq.conf
for unit in flexclock-hotspot.service flexclock-hotspot-dhcp.service; do
  install -o root -g root -m 0644 "$source_dir/$unit" "/etc/systemd/system/$unit"
done
/usr/sbin/dnsmasq --test --conf-file=/usr/local/libexec/flexclock/hotspot-dnsmasq.conf
install -o root -g root -m 0644 /dev/null /etc/flexclock-hotspot-enabled
systemctl daemon-reload
systemctl enable flexclock-hotspot.service
echo 'Fallback installed for the next boot; the live network has not been switched.'
