#!/bin/bash
set -e

Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
echo "Xvfb started (PID $XVFB_PID)"

sleep 1

trap "kill $XVFB_PID 2>/dev/null" EXIT SIGTERM SIGINT

exec "$@"
