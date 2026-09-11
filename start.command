#!/bin/bash
# Double-click this file in Finder to start the app and open it in your browser.
cd "$(dirname "$0")" || exit 1
( sleep 1.5; open "http://localhost:${PORT:-8770}" ) &
exec python3 serve.py
