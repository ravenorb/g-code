#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/ravenorb/g-code.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
TARGET_BASE_DIR="${TARGET_BASE_DIR:-$BASE_DIR}"

command -v git >/dev/null || { echo "git not found"; exit 1; }
command -v docker >/dev/null || { echo "docker not found"; exit 1; }

if ! mkdir -p "$TARGET_BASE_DIR" 2>/dev/null || ! [ -w "$TARGET_BASE_DIR" ]; then
  FALLBACK_BASE_DIR="$HOME/mts"
  echo "WARN: Cannot write to '$TARGET_BASE_DIR' (permission denied)."
  echo "      Falling back to '$FALLBACK_BASE_DIR'."
  mkdir -p "$FALLBACK_BASE_DIR"
  TARGET_BASE_DIR="$FALLBACK_BASE_DIR"
fi

SOURCE_REPO_DIR="$SCRIPT_DIR"
TARGET_REPO_DIR="$TARGET_BASE_DIR/g-code"

echo "=== Reloading g-code repo ==="
echo "Source repo: $SOURCE_REPO_DIR"
echo "Target repo: $TARGET_REPO_DIR"

echo "[1/6] Stopping containers"
if [ -f "$SOURCE_REPO_DIR/docker-compose.yml" ] && [ -f "$SOURCE_REPO_DIR/docker-compose.beta.yml" ]; then
  docker compose -f "$SOURCE_REPO_DIR/docker-compose.yml" -f "$SOURCE_REPO_DIR/docker-compose.beta.yml" down || true
fi

echo "[2/6] Changing to target base directory"
cd "$TARGET_BASE_DIR"

echo "[3/6] Removing old target repo"
rm -rf "$TARGET_REPO_DIR"

echo "[4/6] Cloning fresh repo"
git clone "$REPO_URL" "$TARGET_REPO_DIR"

echo "[5/6] Changing directory"
cd "$TARGET_REPO_DIR"

echo "[6/6] Building and starting main + beta containers"
docker compose -f docker-compose.yml -f docker-compose.beta.yml up --build -d

echo "Done."
