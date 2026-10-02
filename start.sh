#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r DM/requirements.txt
fi
printf '%s\n' 'Open http://127.0.0.1:8766 in your browser. Ctrl+C stops the server.'
exec .venv/bin/python DM/server.py
