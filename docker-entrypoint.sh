#!/bin/sh
set -e

if [ -n "$APP_CONFIG_JSON" ]; then
  mkdir -p /app/application
  printf '%s\n' "$APP_CONFIG_JSON" > /app/application/config.json
fi

exec python -m streamlit run application/app.py --server.port=8501 --server.address=0.0.0.0
