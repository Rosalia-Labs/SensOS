#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo ">> Building and starting the SensOS DinD testbed…"
docker compose build
docker compose up -d

echo ">> Checking inner daemon:"
docker compose exec client sh -lc 'docker version && docker info >/dev/null && echo "OK: client can talk to DinD"'
docker compose exec server sh -lc 'docker ps >/dev/null && echo "OK: server can talk to DinD"'

echo ">> Listing inner networks/containers (from client):"
docker compose exec client sh -lc 'docker network ls; docker ps -a'

echo
echo ">>> Tips"
echo "  - Attach to client shell:  docker compose exec client bash"
echo "  - Inside client, run your inner Compose:"
echo "        docker compose -f /sensos_source/00-sensos/files/docker/docker-compose.yml up -d"
echo "  - Tear down:             docker compose down -v"
