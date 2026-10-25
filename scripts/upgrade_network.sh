#!/usr/bin/env bash
# Bookworm's dhcpcd 9 control interface can accept a rebind without applying it.
# Use the signed Debian 10.1 backport; preserve assigned addresses during handoff.
set -Eeuo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run this setup as root.' >&2; exit 1; }
version="$(dhcpcd --version | head -1 | awk '{print $2}')"
if dpkg --compare-versions "$version" ge 10.1.0; then
  echo 'The installed DHCP client supports address updates.'
  exit 0
fi
. /etc/os-release
[ "$VERSION_CODENAME" = bookworm ] || { echo 'Install a supported DHCP client for this operating system.' >&2; exit 1; }
grep -qE '^[[:space:]]*persistent([[:space:]]|$)' /etc/dhcpcd.conf || { echo 'Address persistence must be configured before replacing the running client.' >&2; exit 1; }
target_version="${DHCPCD_VERSION:-1:10.1.0-11~bpo12+3}"
source_file=/etc/apt/sources.list.d/flexclock-bookworm-backports.list
if [ -e "$source_file" ] && ! grep -qxF 'deb https://deb.debian.org/debian bookworm-backports main' "$source_file"; then
  echo 'Existing package source requires review.' >&2
  exit 1
fi
printf 'deb https://deb.debian.org/debian bookworm-backports main\n' > "$source_file"
apt-get update -o Dir::Etc::sourcelist='sources.list.d/flexclock-bookworm-backports.list' -o Dir::Etc::sourceparts='-' -o APT::Get::List-Cleanup='0'
NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confold install -t bookworm-backports "dhcpcd-base=$target_version" "dhcpcd=$target_version" "dhcpcd5=$target_version"
systemctl daemon-reload
if systemctl is-active --quiet dhcpcd; then
  # Bypass the broken OLD control socket. The existing persistent option keeps
  # kernel addresses/routes installed while the new daemon takes over.
  systemctl kill --kill-who=main --signal=TERM dhcpcd
fi
systemctl start dhcpcd
