#!/bin/bash
cd "$(dirname "$0")"

if [ -n "$WIFE_TERMINAL" ]; then
    # Break out of the VPN namespace so the server is reachable from the host browser
    sudo nsenter -t 1 -n -- sudo -u "$(whoami)" \
        env HOME="$HOME" PATH="$PATH" CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" \
        python3.10 "$(pwd)/app.py" --port 5051
else
    python3.10 app.py --port 5051
fi
