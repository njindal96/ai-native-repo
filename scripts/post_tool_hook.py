#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from sync_context import sync_workspace


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    workspaces = payload.get("workspacePaths") or []
    cwd = payload.get("cwd")
    if cwd and cwd not in workspaces:
        workspaces.append(cwd)

    for ws in workspaces:
        ws_path = Path(ws)
        if (ws_path / "AGENTS.md").exists():
            try:
                sync_workspace(ws_path)
            except Exception:
                pass

    # PostToolUse contract requires an empty JSON object on stdout
    print("{}")


if __name__ == "__main__":
    main()
