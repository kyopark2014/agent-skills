#!/usr/bin/env bash
# Local build + run: frontend (npm) then FastAPI (uvicorn) on :8501
# If port 8501 is already in use, the existing process is stopped first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8501

cd "$ROOT"

echo "==> Frontend build (application/web)"
cd application/web
npm install
npm run build
cd "$ROOT"

echo "==> Freeing port ${PORT} if occupied"
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "    Port ${PORT} in use by PID(s): ${PIDS} — killing"
    # shellcheck disable=SC2086
    kill ${PIDS} 2>/dev/null || true
    sleep 1
    # Force-kill if still listening
    PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${PIDS}" ]]; then
      # shellcheck disable=SC2086
      kill -9 ${PIDS} 2>/dev/null || true
      sleep 0.5
    fi
  else
    echo "    Port ${PORT} is free"
  fi
else
  echo "    lsof not found; skipping port check"
fi

# Prefer python3.13: PATH `uvicorn` / `python3` often point at Homebrew 3.14,
# while pip (aliased to pip3.13) installs into 3.13 site-packages.
if command -v python3.13 >/dev/null 2>&1; then
  PYTHON=python3.13
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  echo "ERROR: python3 not found" >&2
  exit 1
fi

echo "==> Starting uvicorn on 0.0.0.0:${PORT} (${PYTHON})"
echo "    Open http://localhost:${PORT}"
exec "${PYTHON}" -m uvicorn application.server:app --host 0.0.0.0 --port "${PORT}"
