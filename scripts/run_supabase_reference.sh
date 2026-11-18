#!/usr/bin/env bash
# One command from any directory: bash run_supabase_reference.sh /path/to/pianokt [--scope ...]
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-.}"
if [[ $# -gt 0 ]]; then shift; fi
ROOT="$(cd "$ROOT" && pwd)"
if [[ ! -f "$ROOT/backend/pyproject.toml" ]]; then
  echo "Not a PianoKT checkout: $ROOT (requires backend/pyproject.toml)" >&2
  exit 2
fi
if [[ -n "${PIANOKT_PYTHON:-}" ]]; then
  PY="$PIANOKT_PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  echo "Missing PianoKT .venv. Install backend env per docs/LOCAL_DEVELOPMENT_ZH.md (Python 3.13)." >&2
  exit 2
fi
exec "$PY" "$SCRIPT_DIR/sync_supabase_reference.py" --repo "$ROOT" "$@"
