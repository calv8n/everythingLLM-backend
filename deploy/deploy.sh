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
echo "Public endpoint:  http://129.225.68.122:8080  (Caddy -> backend)"
echo "Health check:     curl -i http://129.225.68.122:8080/healthz"
echo "On the VM only:   docker exec ai-task-runner curl -s http://localhost:8000/healthz"
echo "                  (port 8000 is NOT published on the host; only Caddy on :8080 is)"
echo "Users call:                  POST http://129.225.68.122:8080/api/task  with  Authorization: Bearer <their token>"
echo "WebSocket:                   ws://129.225.68.122:8080/ws/<task_id>?token=<their token>"
echo "Logs:                        docker compose logs -f backend"
echo
echo "REMINDER: the OCI VCN Security List / NSG must allow TCP ingress on 8080,"
echo "otherwise the VM-side firewall is irrelevant and requests time out."
