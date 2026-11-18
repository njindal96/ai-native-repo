#!/usr/bin/env python3
"""
PreToolUse Guardrail, Pre-Edit Context Gate & Pre-Push Gate (v3.2 — Pure Python 3 Stdlib)
Supports Antigravity, Claude Code, Cursor, Codex, and Gemini CLI payloads.
Enforces:
1. Pre-Edit Context Gate: tracks reads of AGENTS.md / docs/adr/*.md and blocks blind edits to
   existing layer source files while injecting a single-hop Context Capsule (Why/ADRs/Used By/Tested By).
2. Shell bypass blocking (--no-verify, bundled -n flags on commit/push/merge/cherry-pick/rebase,
   -c core.hooksPath overrides, git config core.hooksPath tampering, guardrail file tampering).
3. git push gate behind full verify_workspace(target, run_tests=True).
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from sync_context import (  # noqa: E402
    build_context_capsule,
    has_context_for_target,
    record_context_read,
    sync_workspace,
)
from verify_architecture import verify_workspace  # noqa: E402

SHELL_TOOL_NAMES = {
    "run_command",
    "Bash",
    "bash",
    "shell",
    "run_shell_command",
    "execute_command",
    "terminal",
}
READ_TOOL_NAMES = {"read_file", "view_file", "Read"}
EDIT_TOOL_NAMES = {"edit_file", "write_file", "Edit", "Write", "MultiEdit"}

GIT_HOOKED_SUBCOMMANDS = {"commit", "push", "merge", "cherry-pick", "rebase"}
GIT_GLOBAL_VALUE_FLAGS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
}
GIT_SUBCMD_VALUE_FLAGS = {
    "-m", "-F", "-C", "-c", "-t", "-s", "-X", "-u", "-S", "--message", "--file",
    "--reuse-message", "--reedit-message", "--fixup", "--squash", "--author", "--date",
    "--cleanup", "--strategy", "--strategy-option", "--template", "--onto",
}
WRAPPERS = {"env", "command", "sudo", "time", "nohup"}


def extract_command(payload: object) -> tuple[bool, str, str]:
    """Defensively extract (uses_claude_format, tool_name, cmd) across 5 agent schemas."""
    if not isinstance(payload, dict):
        return False, "", ""

    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        tool_name = str(tool_call.get("name") or "")
        args = tool_call.get("args")
        cmd = str(args.get("CommandLine") or args.get("command") or "") if isinstance(args, dict) else ""
        return False, tool_name, cmd

    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        cmd = str(tool_input.get("command") or tool_input.get("CommandLine") or tool_input.get("cmd") or "")
    elif isinstance(tool_input, str):
        cmd = tool_input
    else:
        cmd = str(payload.get("command") or "")

    return True, tool_name, cmd


def format_response(is_claude_code: bool, allow: bool, reason: str = "") -> str:
    if is_claude_code:
        if allow:
            return "{}"
        return json.dumps({"decision": "block", "reason": reason})
    if allow:
        return json.dumps({"decision": "allow"})
    return json.dumps({"decision": "deny", "reason": reason})


def split_compound_commands(cmd: str) -> tuple[list[list[str]], bool]:
    """
    Split compound shell commands (&&, ||, ;, |, &, newlines) into token lists using shlex.
    Returns (commands, parse_ok).
    """
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=";&|")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return [], False

    commands: list[list[str]] = []
    current: list[str] = []
    separators = {";", ";;", "&", "&&", "|", "||", "\n"}
    for tok in tokens:
        if tok in separators or set(tok) <= set(";&|\n"):
            if current:
                commands.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        commands.append(current)
    return commands, True


def _strip_wrappers(tokens: list[str]) -> list[str]:
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", tok):
            idx += 1
            continue
        base = Path(tok).name
        if base in WRAPPERS:
            idx += 1
            while idx < len(tokens) and tokens[idx].startswith("-"):
                idx += 1
            continue
        break
    return tokens[idx:]


def _targets_guardrail_path(tok: str) -> bool:
    norm = tok.rstrip("/")
    return (
        ".githooks" in norm.split("/")
        or norm.endswith(".githooks")
        or norm == ".ai-native.json"
        or norm.endswith("/.ai-native.json")
    )


def _check_git_command(tokens: list[str]) -> tuple[Optional[str], bool]:
    """Validate a tokenized git invocation. Returns (block_reason_or_None, is_push)."""
    i = 1
    subcmd: Optional[str] = None

    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            i += 1
            break
        if tok in GIT_GLOBAL_VALUE_FLAGS:
            if tok == "-c" and i + 1 < len(tokens):
                if "core.hookspath" in tokens[i + 1].lower():
                    return (
                        "[ai-native-repo PreToolUse Gate] Overriding `core.hooksPath` via `git -c` is forbidden.",
                        False,
                    )
            i += 2
            continue
        if tok.startswith("-c") and len(tok) > 2:
            if "core.hookspath" in tok[2:].lower():
                return (
                    "[ai-native-repo PreToolUse Gate] Overriding `core.hooksPath` via `git -c` is forbidden.",
                    False,
                )
            i += 1
            continue
        if any(tok.startswith(f + "=") for f in ("--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env")):
            if tok.startswith("--config-env=") and "core.hookspath" in tok.lower():
                return (
                    "[ai-native-repo PreToolUse Gate] Overriding `core.hooksPath` is forbidden.",
                    False,
                )
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        subcmd = tok
        i += 1
        break

    if not subcmd:
        return None, False

    if subcmd == "config":
        rest_lower = [t.lower() for t in tokens[i:]]
        for idx, t in enumerate(rest_lower):
            if t == "core.hookspath" or t.startswith("core.hookspath="):
                is_unset = "--unset" in rest_lower or "--unset-all" in rest_lower
                has_non_githooks_val = idx + 1 < len(rest_lower) and rest_lower[idx + 1] not in (".githooks", "./.githooks")
                if is_unset or has_non_githooks_val or (t.startswith("core.hookspath=") and not t.endswith(".githooks")):
                    return (
                        "[ai-native-repo PreToolUse Gate] Weakening or disabling `core.hooksPath` via `git config` is forbidden.",
                        False,
                    )

    if subcmd in GIT_HOOKED_SUBCOMMANDS:
        j = i
        while j < len(tokens):
            tok = tokens[j]
            if tok == "--":
                break
            if tok in ("--no-verify", "--no-verify=true"):
                return (
                    f"[ai-native-repo PreToolUse Gate] Bypassing git hooks (`{tok}` on `git {subcmd}`) is forbidden.",
                    False,
                )
            if tok.startswith("-") and not tok.startswith("--"):
                flags = tok[1:]
                # Allow `-m"inline msg with spaces"` without misreading message chars as short flags
                is_inline_msg = flags.startswith("m") and len(flags) > 2 and any(c.isspace() for c in flags)
                if not is_inline_msg and "n" in flags:
                    return (
                        f"[ai-native-repo PreToolUse Gate] Bypassing git hooks (`{tok}` on `git {subcmd}`) is forbidden.",
                        False,
                    )
                if tok in GIT_SUBCMD_VALUE_FLAGS or (
                    not is_inline_msg and flags and flags[-1] in {"m", "F", "C", "c", "t", "s", "X", "u", "S"}
                ):
                    j += 2
                    continue
            elif tok in GIT_SUBCMD_VALUE_FLAGS:
                j += 2
                continue
            j += 1

    return None, (subcmd == "push")


def validate_shell_command(cmd: str) -> tuple[Optional[str], bool]:
    """
    Validate shell command against hook bypasses and guardrail tampering.
    Returns (deny_reason_or_None, contains_git_push).
    """
    commands, ok = split_compound_commands(cmd)
    if not ok:
        if "--no-verify" in cmd or "core.hookspath" in cmd.lower():
            return "[ai-native-repo PreToolUse Gate] Malformed shell command containing forbidden bypass flag.", False
        return None, bool(re.search(r"\bgit\b.*\bpush\b", cmd))

    has_push = False
    for raw_tokens in commands:
        tokens = _strip_wrappers(raw_tokens)
        if not tokens:
            continue
        exe = Path(tokens[0]).name

        if exe == "chmod":
            targets = [t for t in tokens[1:] if not t.startswith("-")]
            flags = [t for t in tokens[1:] if t.startswith("-") or re.match(r"^[0-7]{3,4}$", t) or re.match(r"^[ugoa]*[+-=][rwxX]+$", t)]
            touches_guard = any(_targets_guardrail_path(t) for t in targets)
            removes_exec = any(
                ("-x" in f)
                or (re.match(r"^[0-7]{3,4}$", f) and (int(f, 8) & 0o100) == 0)
                for f in flags
            )
            if touches_guard and removes_exec:
                return "[ai-native-repo PreToolUse Gate] Removing executable permission on `.githooks/*` is forbidden.", False

        if exe in {"rm", "unlink", "mv"}:
            if any(_targets_guardrail_path(t) for t in tokens[1:] if not t.startswith("-")):
                return "[ai-native-repo PreToolUse Gate] Deleting or moving `.githooks/*` or `.ai-native.json` is forbidden.", False

        if exe == "git":
            reason, is_push = _check_git_command(tokens)
            if reason:
                return reason, False
            if is_push:
                has_push = True

    return None, has_push


def resolve_git_target(cmd: str, workspaces: list[str]) -> Optional[Path]:
    try:
        tokens = shlex.split(cmd)
        for i, tok in enumerate(tokens):
            if tok == "-C" and i + 1 < len(tokens):
                candidate = Path(tokens[i + 1]).expanduser().resolve()
                if candidate.exists():
                    return candidate
            elif tok.startswith("-C") and len(tok) > 2:
                candidate = Path(tok[2:]).expanduser().resolve()
                if candidate.exists():
                    return candidate
    except Exception:
        pass
    for ws in workspaces:
        p = Path(ws).resolve()
        if (p / "AGENTS.md").exists():
            return p
    return Path(workspaces[0]).resolve() if workspaces else None


def extract_file_target(payload: object) -> str:
    """Extract target file path across Antigravity, Claude Code, Cursor, Codex, and Gemini CLI schemas."""
    if not isinstance(payload, dict):
        return ""
    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        args = tool_call.get("args")
        if isinstance(args, dict):
            return str(args.get("Path") or args.get("TargetFile") or args.get("file_path") or args.get("path") or "")
        return ""
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        return str(tool_input.get("file_path") or tool_input.get("path") or tool_input.get("TargetFile") or tool_input.get("Path") or "")
    return str(payload.get("file_path") or payload.get("path") or "")


def _collect_candidate_workspaces(payload: object) -> list[Path]:
    if not isinstance(payload, dict):
        return []
    ws_strs: list[str] = []
    raw_ws = payload.get("workspacePaths")
    if isinstance(raw_ws, list):
        ws_strs.extend(str(w) for w in raw_ws if w)
    for key in ("workspace_roots", "workspaces"):
        raw = payload.get(key)
        if isinstance(raw, list):
            ws_strs.extend(str(w) for w in raw if w)
    for key in ("cwd", "workspace", "workspaceRoot"):
        val = payload.get(key)
        if isinstance(val, str) and val and val not in ws_strs:
            ws_strs.insert(0, val)
    out: list[Path] = []
    for s in ws_strs:
        try:
            p = Path(s).expanduser().resolve()
            if p.is_dir() and p not in out:
                out.append(p)
        except Exception:
            pass
    return out


def resolve_workspace_and_rel(file_target: str, payload: object) -> tuple[Optional[Path], str]:
    if not file_target:
        return None, ""
    candidates = _collect_candidate_workspaces(payload)
    p = Path(file_target).expanduser()
    if p.is_absolute():
        try:
            p_res = p.resolve()
        except Exception:
            p_res = p
        for ws in candidates:
            try:
                rel = p_res.relative_to(ws).as_posix()
                if (ws / "AGENTS.md").exists() or (ws / ".ai-native.json").exists():
                    return ws, rel
            except ValueError:
                continue
        cur = p_res.parent
        for _ in range(12):
            if (cur / ".ai-native.json").is_file() or (cur / "AGENTS.md").is_file():
                try:
                    return cur, p_res.relative_to(cur).as_posix()
                except ValueError:
                    break
            if cur.parent == cur:
                break
            cur = cur.parent
        return None, ""

    for ws in candidates:
        if (ws / ".ai-native.json").exists() or (ws / "AGENTS.md").exists():
            return ws, p.as_posix().lstrip("./")
    if candidates:
        return candidates[0], p.as_posix().lstrip("./")
    return None, ""


def _is_gated_layer_source(ws_path: Path, rel_path: str, config: dict) -> bool:
    if not config.get("enforce_pre_edit_context", True):
        return False
    norm = Path(rel_path).as_posix().lstrip("./")
    name = Path(norm).name
    if name in {"AGENTS.md", "CLAUDE.md", ".ai-native.json"}:
        return False
    if any(norm.startswith(prefix) for prefix in ("docs/", "tests/", "scripts/", ".githooks/")):
        return False
    if not (ws_path / norm).is_file():
        return False
    for layer in config.get("layers", []):
        lp = str(layer.get("path") or "").strip().strip("/")
        if lp and (norm == lp or norm.startswith(lp + "/")):
            return True
    return False


def _record_shell_context_reads(cmd: str, payload: object) -> None:
    commands, ok = split_compound_commands(cmd)
    if not ok:
        return
    candidates = _collect_candidate_workspaces(payload)
    ws_path: Optional[Path] = next(
        (c for c in candidates if (c / "AGENTS.md").exists() or (c / ".ai-native.json").exists()),
        candidates[0] if candidates else None,
    )
    if ws_path is None:
        return
    context_tokens: list[str] = []
    for raw_tokens in commands:
        for tok in raw_tokens:
            if tok.endswith("AGENTS.md") or "docs/adr/" in tok:
                ws, rel = resolve_workspace_and_rel(tok, payload)
                if ws == ws_path and rel:
                    context_tokens.append(rel)
    if context_tokens:
        record_context_read(ws_path, context_tokens)


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    is_claude_code, tool_name, cmd = extract_command(payload)

    if tool_name in READ_TOOL_NAMES:
        file_target = extract_file_target(payload)
        ws_path, rel_path = resolve_workspace_and_rel(file_target, payload)
        if ws_path and rel_path:
            record_context_read(ws_path, [rel_path])
        print(format_response(is_claude_code, True))
        return

    if tool_name in EDIT_TOOL_NAMES:
        file_target = extract_file_target(payload)
        ws_path, rel_path = resolve_workspace_and_rel(file_target, payload)
        if ws_path and rel_path and (ws_path / ".ai-native.json").is_file():
            try:
                config = json.loads((ws_path / ".ai-native.json").read_text(encoding="utf-8"))
            except Exception:
                config = {}
            if isinstance(config, dict) and _is_gated_layer_source(ws_path, rel_path, config):
                ok, _ = has_context_for_target(ws_path, rel_path)
                if not ok:
                    capsule = build_context_capsule(ws_path, rel_path, mark_ledger=True)
                    print(format_response(
                        is_claude_code,
                        False,
                        f"[ai-native-repo Pre-Edit Context Gate] Hold on — before editing `{rel_path}`, "
                        "review its governing Why/ADR rationale, callers, and tests below "
                        "(context is now unlocked for your next edit call):\n\n" + capsule,
                    ))
                    return
        print(format_response(is_claude_code, True))
        return

    if tool_name not in SHELL_TOOL_NAMES or not cmd:
        print(format_response(is_claude_code, True))
        return

    _record_shell_context_reads(cmd, payload)

    deny_reason, has_push = validate_shell_command(cmd)
    if deny_reason:
        print(format_response(is_claude_code, False, deny_reason))
        return

    if has_push and isinstance(payload, dict):
        raw_ws = payload.get("workspacePaths")
        workspaces = [str(w) for w in raw_ws] if isinstance(raw_ws, list) else []
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd and cwd not in workspaces:
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
