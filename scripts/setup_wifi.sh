#!/usr/bin/env bash
# Configure only wlan0 for the installed dhcpcd/wpa_supplicant stack.
set -Eeuo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run this setup as root.' >&2; exit 1; }
if systemctl is-active --quiet NetworkManager || ! systemctl is-active --quiet dhcpcd; then
  echo 'This Wi-Fi setup requires the existing dhcpcd network stack. No network manager was changed.' >&2
  exit 1
fi
bash "$(dirname "$(readlink -f "$0")")/upgrade_network.sh"
install -d -m 0755 /etc/systemd/system/wpa_supplicant@wlan0.service.d
printf '[Service]\nUMask=0077\n' > /etc/systemd/system/wpa_supplicant@wlan0.service.d/privacy.conf
chmod 0644 /etc/systemd/system/wpa_supplicant@wlan0.service.d/privacy.conf
systemctl daemon-reload
if [ -f /etc/wpa_supplicant/wpa_supplicant-wlan0.conf ]; then
  chmod 0600 /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
fi
if wpa_cli -i wlan0 ping 2>/dev/null | grep -qx PONG; then
  echo 'Wi-Fi control is already available; existing configuration preserved.'
  exit 0
fi
python3 - <<'PY'
import os
import re
import subprocess
from pathlib import Path
source = Path('/etc/wpa_supplicant/wpa_supplicant.conf')
target = Path('/etc/wpa_supplicant/wpa_supplicant-wlan0.conf')
text = target.read_text() if target.exists() else source.read_text() if source.exists() else ''
for key, value in [('ctrl_interface','DIR=/run/wpa_supplicant GROUP=netdev'),('update_config','1')]:
    pattern = rf'(?m)^{key}=.*$'
    text = re.sub(pattern, key+'='+value, text) if re.search(pattern,text) else key+'='+value+'\n'+text
if not re.search(r'(?m)^country=[A-Z]{2}$',text):
    result = subprocess.run(['iw','reg','get'],capture_output=True,text=True,check=True)
    match = re.search(r'(?m)^country ([A-Z]{2}):',result.stdout)
    if not match:
        raise SystemExit('Set the Wi-Fi country in the operating system before running Wi-Fi setup.')
    text = 'country='+match[1]+'\n'+text
os.makedirs(target.parent,exist_ok=True)
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as stream:stream.write(text)
os.chmod(target,0o600)
PY
systemctl enable wpa_supplicant@wlan0
# This unit manages only wlan0. Ethernet and the shared DHCP daemon are not restarted.
systemctl restart wpa_supplicant@wlan0
for attempt in 1 2 3 4 5; do
  if wpa_cli -i wlan0 ping 2>/dev/null | grep -qx PONG; then
    echo 'Wi-Fi is ready.'
    exit 0
  fi
  sleep 1
done
echo 'Wi-Fi did not become ready. Check the interface service log.' >&2
exit 1
