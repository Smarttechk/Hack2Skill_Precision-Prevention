#!/bin/sh
# Pull the latest code and restart. Run as root from anywhere.
set -eu
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
git pull --ff-only
venv/bin/pip install -r requirements.txt
systemctl restart hack2skill
sleep 3
curl -ks https://127.0.0.1/api/health || true
echo
