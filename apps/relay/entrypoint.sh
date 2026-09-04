#!/bin/sh
set -eu

Xvfb "${DISPLAY:-:99}" -screen 0 1280x720x24 -nolisten tcp &
sleep 0.2
exec "$@"
