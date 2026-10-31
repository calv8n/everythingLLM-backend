#!/usr/bin/env bash
# Build & start the AI Task Runner demo stack on the provisioned OCI VM.
# From the repo root on the server:  bash deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env.production ]; then
  cp .env.production.example .env.production
  echo "Created .env.production — EDIT IT (DEMO_TOKENS, FRONTEND_ORIGIN), then re-run."
  echo "Suggested per-user tokens (one line each):"
  for i in 1 2 3 4 5 6; do openssl rand -base64 24 | tr '+/' '-_' | tr -d '='; done
  exit 1
fi

# DOCKER_GID lets the app container talk to /var/run/docker.sock
export DOCKER_GID="$(getent group docker | cut -d: -f3)"

docker compose up -d --build

echo
echo "Backend health (on the VM):  curl http://localhost:8000/healthz"
echo "Users call:                  POST /api/task  with  Authorization: Bearer <their token>"
echo "WebSocket:                   ws://<host>/ws/<task_id>?token=<their token>"
echo "Logs:                        docker compose logs -f backend"
