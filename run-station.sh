#!/bin/sh
set -eu
port=${1:-8002}
if [ "$#" -gt 1 ]; then
    echo "Usage: ./run-station.sh [port]" >&2
    exit 2
fi
case "$port" in ''|*[!0-9]*) echo "Ungültiger Port" >&2; exit 2;; esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then exit 2; fi
cd "$(dirname "$0")"
PATH="$HOME/.local/bin:$HOME/bin:$PATH"
export PATH
unset PYTHONPATH
exec uv run --locked --no-dev uvicorn nfc_station.app:create_app \
    --factory --host 0.0.0.0 --port "$port" --workers 1 --no-access-log
