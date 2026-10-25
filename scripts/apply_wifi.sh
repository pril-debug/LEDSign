#!/usr/bin/env bash
set -Eeuo pipefail
# Retired unsafe implementation: compatibility entrypoint to the commissioned helper.
exec /usr/local/libexec/flexclock/web_network.py wifi
