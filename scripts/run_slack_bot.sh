#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

pkill -f "slack_bot.app" 2>/dev/null || true
pkill -f "python -m slack_bot" 2>/dev/null || true
sleep 1

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m slack_bot.app
