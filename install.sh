#!/usr/bin/env bash
set -euo pipefail

PLUGIN_DIR="${AI_NATIVE_PLUGIN_DIR:-$HOME/.gemini/config/plugins/ai-native-repo}"
REPO_URL="https://github.com/njindal96/ai-native-repo.git"

echo "==> Installing AI-Native Repo Context Engine to: $PLUGIN_DIR"
mkdir -p "$(dirname "$PLUGIN_DIR")"

if [ -d "$PLUGIN_DIR/.git" ]; then
  echo "==> Existing installation found; pulling latest updates..."
  git -C "$PLUGIN_DIR" pull --ff-only origin main
elif [ -d "$PLUGIN_DIR" ]; then
  echo "==> Plugin directory already exists at $PLUGIN_DIR."
else
  git clone "$REPO_URL" "$PLUGIN_DIR"
fi

chmod +x "$PLUGIN_DIR/scripts/"*.py "$PLUGIN_DIR/install.sh" 2>/dev/null || true
echo "==> Global plugin installed at $PLUGIN_DIR"

if [ "${1:-}" = "--init" ] && [ -n "${2:-}" ]; then
  TARGET_REPO="$2"
  echo "==> Bootstrapping repository: $TARGET_REPO"
  python3 "$PLUGIN_DIR/scripts/bootstrap_repo.py" --workspace "$TARGET_REPO"
fi

echo "==> Done! Open any project in Antigravity (or run: python3 $PLUGIN_DIR/scripts/bootstrap_repo.py --workspace /path/to/repo)."
