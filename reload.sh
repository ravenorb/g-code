#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/ravenorb/g-code.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$BASE_DIR/g-code"

command -v git >/dev/null || { echo "git not found"; exit 1; }
command -v docker >/dev/null || { echo "docker not found"; exit 1; }

echo "=== Reloading g-code repo ==="

echo "[1/6] Stopping containers"
docker compose -f "$REPO_DIR/docker-compose.yml" -f "$REPO_DIR/docker-compose.beta.yml" down || true

echo "[2/6] Changing to base directory"
cd "$BASE_DIR"

echo "[3/6] Removing old repo"
rm -rf "$REPO_DIR"

echo "[4/6] Cloning fresh repo"
git clone "$REPO_URL" "$REPO_DIR"

echo "[5/6] Changing directory"
cd "$REPO_DIR"

echo "[6/6] Building and starting main + beta containers"
docker compose -f docker-compose.yml -f docker-compose.beta.yml up --build -d
