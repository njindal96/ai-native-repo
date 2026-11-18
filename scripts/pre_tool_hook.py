#!/usr/bin/env python3
"""
PreToolUse Guardrail & Pre-Push Gate (v3.0)
Supports Antigravity (run_command) and Claude Code (Bash).
Blocks:
1. Git hook bypass attempts (--no-verify, git commit -n)
2. git push when verify_architecture.py fails
"""
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Optional
from sync_context import sync_workspace
from verify_architecture import verify_workspace


def extract_command(payload: dict) -> tuple[bool, str, str]:
    is_claude_code = "tool_name" in payload and "toolCall" not in payload
    if is_claude_code:
        tool_name = payload.get("tool_name", "")
        cmd = (payload.get("tool_input") or {}).get("command", "")
        return True, tool_name, cmd
    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args") or {}
    cmd = args.get("CommandLine", "")
    return False, tool_name, cmd


def format_response(is_claude_code: bool, allow: bool, reason: str = "") -> str:
    if is_claude_code:
        if allow:
            return "{}"
        return json.dumps({"decision": "block", "reason": reason})
    if allow:
        return json.dumps({"decision": "allow"})
    return json.dumps({"decision": "deny", "reason": reason})


def resolve_git_target(cmd: str, workspaces: list[str]) -> Optional[Path]:
    try:
        tokens = shlex.split(cmd)
        for i, tok in enumerate(tokens):
            if tok == "-C" and i + 1 < len(tokens):
                candidate = Path(tokens[i + 1]).expanduser().resolve()
                if candidate.exists():
                    return candidate
    except Exception:
        pass
    for ws in workspaces:
        p = Path(ws).resolve()
        if (p / "AGENTS.md").exists():
            return p
    return Path(workspaces[0]).resolve() if workspaces else None


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    is_claude_code, tool_name, cmd = extract_command(payload)
    if tool_name not in ("run_command", "Bash") or not cmd:
        print(format_response(is_claude_code, True))
        return

    # 1. Hard-block attempts to bypass git hooks
    if re.search(r"(^|\s)--no-verify(\s|$)", cmd) or re.search(r"\bgit\s+commit\b[^;&|]*\s-n(\s|$)", cmd):
        print(format_response(
            is_claude_code,
            False,
            "[ai-native-repo PreToolUse Gate] Bypassing git hooks (--no-verify / -n) is forbidden.",
        ))
        return

    # 2. Gate git push behind full architecture & test verification
    if re.search(r"\bgit\b[^;&|]*\bpush\b", cmd):
        workspaces = list(payload.get("workspacePaths") or [])
        cwd = payload.get("cwd")
        if cwd and cwd not in workspaces:
            workspaces.insert(0, cwd)

        target = resolve_git_target(cmd, workspaces)
        if target and (target / "AGENTS.md").exists():
            try:
                sync_workspace(target)
            except Exception:
                pass
            errs = verify_workspace(target, run_tests=True)
            if errs:
                print(format_response(
                    is_claude_code,
                    False,
                    "[ai-native-repo PreToolUse Gate] `git push` blocked — verification failed:\n"
                    + "\n".join(errs[:5]),
                ))
                return

    print(format_response(is_claude_code, True))


if __name__ == "__main__":
    main()
