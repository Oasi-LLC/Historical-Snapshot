#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
export SNAPSHOT_API_URL="${SNAPSHOT_API_URL:-http://127.0.0.1:8000}"
# Default 8502 avoids Streamlit's built-in default (8501), which is often already in use.
PORT="${1:-${DASHBOARD_PORT:-8502}}"
exec streamlit run dashboard/app.py --server.port "$PORT" --server.address 127.0.0.1
