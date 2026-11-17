#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from pathlib import Path
from sync_context import END_MARKER, START_MARKER, sync_workspace
from verify_architecture import verify_workspace


def split_semantic_and_auto(text: str) -> tuple[str, str]:
    start_m = re.search(rf"^[ \t]*{re.escape(START_MARKER)}[ \t]*$", text, re.M)
    end_m = re.search(rf"^[ \t]*{re.escape(END_MARKER)}[ \t]*$", text, re.M)
    if not start_m or not end_m or end_m.start() <= start_m.end():
        return text.strip(), ""
    before = text[:start_m.start()]
    auto_body = text[start_m.end():end_m.start()]
    after = text[end_m.end():]
    return (before + "\n" + after).strip(), auto_body.strip()


def check_semantic_drift(ws_path: Path) -> list[str]:
    """
    If a tracked AGENTS.md file has changes inside its AUTO-GENERATED contract block
    compared to git HEAD (meaning classes, functions, or fields were added/removed/changed),
    verify whether the semantic section of that AGENTS.md was also updated.
    """
    drifted = []
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", "*AGENTS.md", "AGENTS.md"],
            cwd=str(ws_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        changed_files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        return []

    for rel_path in changed_files:
        if rel_path == "AGENTS.md":
            # Root AGENTS.md auto-updates line counts; only enforce semantic drift on layer AGENTS.md files
            continue
        full_path = ws_path / rel_path
        if not full_path.exists():
            continue
        try:
            head_proc = subprocess.run(
                ["git", "show", f"HEAD:{rel_path}"],
                cwd=str(ws_path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if head_proc.returncode != 0:
                continue
            old_sem, old_auto = split_semantic_and_auto(head_proc.stdout)
            new_sem, new_auto = split_semantic_and_auto(full_path.read_text(encoding="utf-8"))
            if old_auto != new_auto and old_sem == new_sem:
                drifted.append(rel_path)
        except Exception:
            continue
    return drifted


def format_response(is_claude_code: bool, allow: bool, reason: str = "") -> str:
    if is_claude_code:
        if allow:
            return "{}"
        return json.dumps({"decision": "block", "reason": reason})
    if allow:
        return json.dumps({"decision": "allow"})
    return json.dumps({"decision": "continue", "reason": reason})


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    is_claude_code = "stop_hook_active" in payload
    if is_claude_code:
        if payload.get("stop_hook_active"):
            print(format_response(True, True))
            return
    elif payload.get("continuationActive") or payload.get("terminationReason") != "NO_TOOL_CALL":
        print(format_response(False, True))
        return

    workspaces = payload.get("workspacePaths") or []
    cwd = payload.get("cwd")
    if cwd and cwd not in workspaces:
        workspaces.append(cwd)

    for ws in workspaces:
        ws_path = Path(ws)
        if not (ws_path / "AGENTS.md").exists():
            continue

        # 1. Sync all AGENTS.md auto-generated blocks
        try:
            sync_workspace(ws_path)
        except Exception:
            pass

        # 2. Verify syntax, layer import boundaries, context budgets, and unit tests
        errs = verify_workspace(ws_path, run_tests=True)
        if errs:
            print(format_response(
                is_claude_code,
                False,
                "[ai-native-repo Stop Gate] Verification failed:\n" + "\n".join(errs[:5]),
            ))
            return

        # 3. Check for semantic drift on scoped AGENTS.md files
        drifted = check_semantic_drift(ws_path)
        if drifted:
            print(format_response(
                is_claude_code,
                False,
                (
                    f"[ai-native-repo Stop Gate] Public code contracts changed in {', '.join(drifted)}, "
                    "but the semantic notes outside the <!-- AUTO-GENERATED --> block were not updated. "
                    "Please review and update the semantic section of those AGENTS.md file(s) before finishing."
                ),
            ))
            return

    print(format_response(is_claude_code, True))


if __name__ == "__main__":
    main()
