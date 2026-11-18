#!/usr/bin/env python3
"""
PostToolUse Contract Sync Hook (v3.1)
Defensively extracts workspace paths across Antigravity, Claude Code, Cursor, Codex, and Gemini CLI,
then re-syncs AGENTS.md auto-generated contract blocks with zero crashes on arbitrary/malformed payloads.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from sync_context import sync_workspace  # noqa: E402


def extract_workspaces(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    workspaces: list[str] = []
    for key in ("workspacePaths", "workspace_roots", "workspaces"):
        val = payload.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item and item not in workspaces:
                    workspaces.append(item)
    for key in ("cwd", "workspace", "workspaceRoot"):
        val = payload.get(key)
        if isinstance(val, str) and val and val not in workspaces:
            workspaces.append(val)
    return workspaces


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    for ws in extract_workspaces(payload):
        ws_path = Path(ws)
        if (ws_path / "AGENTS.md").exists():
            try:
                sync_workspace(ws_path)
            except Exception:
                pass

    # PostToolUse contract expects an empty JSON object on stdout
    print("{}")


if __name__ == "__main__":
    main()
