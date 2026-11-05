
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

# Local API writes can never claim production provenance.
export PIANOKT_ENV=development

cd backend

exec uvicorn pianokt_backend.api:app \
  --reload \
  --reload-dir pianokt_backend \
  --host 127.0.0.1 \
  --port 8000
