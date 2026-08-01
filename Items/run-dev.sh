#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> Installing common library to local Maven repo..."
(cd "$ROOT_DIR/common" && ./mvnw install -DskipTests -q)

echo "==> Starting Items service with DevTools (auto-restart on class changes)..."
echo "    Edit any .java file, save, and DevTools will restart the context."
echo "    Infra (DB, Redis, Kafka) must be running in Docker:"
echo "      docker compose up -d items-postgres redis kafka"
echo ""

cd "$SCRIPT_DIR"
exec ./mvnw spring-boot:run
