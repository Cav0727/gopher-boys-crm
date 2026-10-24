#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d venv ]]; then
  python3 -m venv venv
  ./venv/bin/pip install -r requirements.txt
fi
./venv/bin/pip install -q -r requirements.txt
export PORT="${PORT:-5055}"
exec ./venv/bin/python app.py
