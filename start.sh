#!/bin/sh
set -eu
cd /app/backend
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/aegistel-backend.log 2>&1 &
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
cd /app/frontend
export HOSTNAME=0.0.0.0
export PORT=${PORT:-7860}
exec node server.js
