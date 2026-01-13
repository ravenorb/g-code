#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/ravenorb/g-code.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$BASE_DIR/g-code"

command -v git >/dev/null || { echo "git not found"; exit 1; }
command -v docker >/dev/null || { echo "docker not found"; exit 1; }

echo "=== Reloading g-code repo ==="

echo "[1/7] Stopping containers"
docker compose -f "$REPO_DIR/docker-compose.yml" down || true
docker compose -f "$REPO_DIR/docker-compose.beta.yml" down || true

echo "[2/7] Changing to base directory"
cd "$BASE_DIR"

echo "[3/7] Removing old repo"
rm -rf "$REPO_DIR"

echo "[4/7] Cloning fresh repo"
git clone "$REPO_URL" "$REPO_DIR"

echo "[5/7] Changing directory"
cd "$REPO_DIR"

echo "[6/7] Building and starting main containers"
docker compose up --build -d

echo "[7/7] Building and starting beta containers"
docker compose -f docker-compose.beta.yml up --build -d
