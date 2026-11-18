#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f backend/.env.local ]]; then
  echo "Missing backend/.env.local"
  exit 1
fi
source .venv/bin/activate
set -a
source backend/.env.local
set +a
export PYTHONPATH="$PWD/backend${PYTHONPATH:+:$PYTHONPATH}"
exec python backend/diagnostics/db_roundtrip.py "$@"
