#!/usr/bin/env bash
# Install only the controller startup configuration; leave web/network units alone.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
install -d -m 0755 /etc/systemd/system/sign-ui.service.d
cat > /etc/systemd/system/sign-ui.service.d/boot-menu.conf <<EOF
[Service]
User=root
WorkingDirectory=${ROOT_DIR}
ExecStart=
ExecStart=${ROOT_DIR}/venv/bin/python -m boot.splash
StandardInput=tty
StandardOutput=journal
StandardError=journal
TTYPath=/dev/tty8
TTYReset=yes
TTYVHangup=yes
ExecStartPre=+/usr/bin/chvt 8
EOF
# Dashboard mode control uses config/controller.sock, owned by the config owner.
# Keep the existing config ownership: sign-web must run as that same user.
systemctl daemon-reload
systemctl enable sign-ui
