#!/usr/bin/env python3
"""
PostToolUse Contract Sync Hook & Silent Friction Telemetry Collector (v3.3)
1. Defensively extracts workspace paths and tool telemetry across Antigravity, Claude Code,
   Cursor, Codex, and Gemini CLI with zero crashes on arbitrary/malformed payloads.
2. Collects zero-token friction/pattern telemetry in a temporary per-workspace ledger.
3. Re-syncs AGENTS.md auto-generated contract blocks whenever AGENTS.md exists.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from sync_context import (  # noqa: E402
    IGNORE_DIRS,
    POLYGLOT_EXTENSIONS,
    adr_matches_path,
    load_all_adrs,
    sync_workspace,
)

SOURCE_CODE_EXTENSIONS = {".py"} | POLYGLOT_EXTENSIONS
STACK_MANIFEST_FILES = (
    "package.json",
    "pyproject.toml",
    "setup.py",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)
NON_STACK_TOP_DIRS = {
    "tests",
    "test",
    "scripts",
    "docs",
    ".github",
    ".githooks",
    ".agents",
    "skills",
    "rules",
    "assets",
    "public",
}

READ_SEARCH_TOOLS = {
    "read_file",
    "view_file",
    "Read",
    "grep_search",
    "find_by_name",
    "Grep",
    "Glob",
}
EDIT_TOOLS = {"edit_file", "write_file", "Edit", "Write", "MultiEdit"}
SHELL_TOOLS = {
    "run_command",
    "Bash",
    "bash",
    "shell",
    "run_shell_command",
    "execute_command",
    "terminal",
}
TRIVIAL_SHELL_PREFIXES = ("git status", "git diff", "git log", "pwd", "ls", "echo ", "true")
POLL_SHELL_PREFIXES = (
    "gh run",
    "gh pr checks",
    "gh workflow",
    "curl ",
    "wget ",
    "kubectl get",
    "kubectl logs",
    "docker ps",
    "docker compose ps",
    "systemctl status",
    "pm2 status",
)


def _get_poll_prefixes(ws_path: Path) -> tuple[str, ...]:
    prefixes = list(POLL_SHELL_PREFIXES)
    cfg_path = ws_path / ".ai-native.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            custom = cfg.get("poll_command_prefixes")
            if isinstance(custom, list):
                for item in custom:
                    if isinstance(item, str) and item.strip():
                        prefixes.append(item.strip().lower())
        except Exception:
            pass
    return tuple(prefixes)


def auto_upgrade_repo_scripts(ws_path: Path) -> list[str]:
    """
    Propagate updated plugin scripts into onboarded repositories when the global
    ai-native-repo plugin is newer than the workspace's copies.
    """
    upgraded: list[str] = []
    if not (ws_path / ".ai-native.json").is_file() or not (ws_path / "scripts").is_dir():
        return upgraded
    plugin_scripts = Path(_SCRIPT_DIR).resolve()
    ws_res = ws_path.resolve()
    if ws_res == plugin_scripts.parent:
        return upgraded

    for script_name in ("sync_context.py", "verify_architecture.py"):
        src = plugin_scripts / script_name
        dst = ws_res / "scripts" / script_name
        if not src.is_file() or not dst.is_file():
            continue
        try:
            if src.resolve() != dst.resolve() and src.read_bytes() != dst.read_bytes():
                shutil.copy2(src, dst)
                mode = dst.stat().st_mode
                dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                upgraded.append(f"scripts/{script_name}")
        except OSError:
            pass
    return upgraded


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


def get_telemetry_path(ws_path: Path) -> Path:
    digest = hashlib.sha256(str(ws_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"ai_native_telemetry_{digest}.json"


def _default_telemetry(now: float) -> dict:
    return {
        "updated_at": now,
        "last_tool_category": "",
        "last_edited_file": "",
        "exploratory_reads_before_edit": 0,
        "first_edit_done": False,
        "adr_or_agents_edited": False,
        "layer_file_edits": {},
        "unregistered_stack_edits": {},
        "oversized_file_edits": {},
        "shell_history": [],
        "command_ngrams": {},
        "poll_commands": {},
        "cooldowns_fired": {},
    }


def load_telemetry(ws_path: Path, ttl_seconds: int = 14400) -> dict:
    now = time.time()
    path = get_telemetry_path(ws_path)
    base = _default_telemetry(now)
    if not path.is_file():
        return base
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return base
        if (now - float(data.get("updated_at", 0.0))) > ttl_seconds:
            return base
        for k, default_v in base.items():
            val = data.get(k)
            if type(val) is type(default_v):
                base[k] = val
        return base
    except Exception:
        return base


def save_telemetry(ws_path: Path, state: dict) -> None:
    state["updated_at"] = time.time()
    path = get_telemetry_path(ws_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def extract_tool_event(payload: object) -> tuple[str, str, str]:
    """Defensively extract (tool_name, file_target, command) across 5 agent payload formats."""
    if not isinstance(payload, dict):
        return "", "", ""

    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        tool_name = str(tool_call.get("name") or "")
        args = tool_call.get("args")
        if isinstance(args, dict):
            file_target = str(
                args.get("Path")
                or args.get("TargetFile")
                or args.get("file_path")
                or args.get("path")
                or args.get("SearchPath")
                or args.get("DirectoryPath")
                or ""
            )
            cmd = str(args.get("CommandLine") or args.get("command") or "")
        else:
            file_target, cmd = "", ""
        return tool_name, file_target, cmd

    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        file_target = str(
            tool_input.get("file_path")
            or tool_input.get("path")
            or tool_input.get("TargetFile")
            or tool_input.get("Path")
            or ""
        )
        cmd = str(tool_input.get("command") or tool_input.get("CommandLine") or tool_input.get("cmd") or "")
    elif isinstance(tool_input, str):
        file_target, cmd = "", tool_input
    else:
        file_target = str(payload.get("file_path") or payload.get("path") or "")
        cmd = str(payload.get("command") or "")
    return tool_name, file_target, cmd


def normalize_shell_command(cmd: str) -> str:
    norm = " ".join((cmd or "").split()).strip()
    if not norm:
        return ""
    lower = norm.lower()
    if any(lower == p.strip() or lower.startswith(p) for p in TRIVIAL_SHELL_PREFIXES):
        return ""
    return norm[:180]


def _to_rel_path(ws_path: Path, file_target: str) -> str:
    if not file_target:
        return ""
    p = Path(file_target).expanduser()
    ws_res = ws_path.resolve()
    if p.is_absolute():
        try:
            return p.resolve().relative_to(ws_res).as_posix()
        except Exception:
            return p.as_posix()
    return p.as_posix().lstrip("./")


def _load_ai_native_config(ws_path: Path) -> Optional[dict]:
    cfg_path = ws_path / ".ai-native.json"
    if not cfg_path.is_file():
        return None
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _is_non_test_code_source(rel_path: str) -> bool:
    if not rel_path:
        return False
    norm = Path(rel_path).as_posix().lstrip("./")
    p = Path(norm)
    name = p.name
    if name in {"AGENTS.md", "CLAUDE.md", ".ai-native.json"} or norm.endswith(".d.ts"):
        return False
    if norm.endswith(("config.ts", "config.mjs", "config.js")):
        return False
    if p.suffix.lower() not in SOURCE_CODE_EXTENSIONS:
        return False
    if any(norm.startswith(prefix) for prefix in ("docs/", "tests/", "test/", "scripts/", ".githooks/")):
        return False
    if "/tests/" in f"/{norm}" or "/test/" in f"/{norm}" or "/__tests__/" in f"/{norm}":
        return False
    if name.startswith("test_") or name.endswith((
        ".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx",
        ".test.js", ".spec.js", ".test.jsx", ".spec.jsx",
        "_test.go", "_test.rs", "Test.java", "Tests.java",
    )):
        return False
    return True


def _is_layer_source(ws_path: Path, rel_path: str, cfg: Optional[dict] = None) -> bool:
    if not rel_path:
        return False
    norm = Path(rel_path).as_posix().lstrip("./")
    if Path(norm).name in {"AGENTS.md", "CLAUDE.md", ".ai-native.json"}:
        return False
    if any(norm.startswith(prefix) for prefix in ("docs/", "tests/", "scripts/", ".githooks/")):
        return False
    loaded = cfg if cfg is not None else _load_ai_native_config(ws_path)
    if loaded is None:
        return False
    for layer in loaded.get("layers", []):
        lp = str(layer.get("path") or "").strip().strip("/")
        if lp and (norm == lp or norm.startswith(lp + "/")):
            return True
    return False


def _has_dir_stack_manifest_or_sources(dir_path: Path) -> bool:
    if not dir_path.is_dir():
        return False
    for mf in STACK_MANIFEST_FILES:
        if (dir_path / mf).is_file():
            return True
    src_count = 0
    for item in dir_path.rglob("*"):
        if not item.is_file() or item.name.endswith(".d.ts"):
            continue
        if any(part in IGNORE_DIRS for part in item.relative_to(dir_path).parts):
            continue
        if item.suffix.lower() in SOURCE_CODE_EXTENSIONS:
            src_count += 1
            if src_count >= 3:
                return True
    return False


def record_tool_telemetry(ws_path: Path, payload: object) -> None:
    tool_name, file_target, cmd = extract_tool_event(payload)
    if not tool_name:
        return

    state = load_telemetry(ws_path)
    if tool_name in READ_SEARCH_TOOLS:
        if not state["first_edit_done"]:
            state["exploratory_reads_before_edit"] += 1
        state["last_tool_category"] = "read"
        save_telemetry(ws_path, state)
        return

    if tool_name in EDIT_TOOLS:
        state["first_edit_done"] = True
        rel_path = _to_rel_path(ws_path, file_target)
        if rel_path:
            cfg = _load_ai_native_config(ws_path)
            if Path(rel_path).name in {"AGENTS.md", "CLAUDE.md"} or rel_path.startswith("docs/adr/"):
                state["adr_or_agents_edited"] = True
            elif _is_layer_source(ws_path, rel_path, cfg):
                entry = state["layer_file_edits"].setdefault(
                    rel_path,
                    {"edit_count": 0, "rework_cycles": 0, "reverted": False},
                )
                entry["edit_count"] = int(entry.get("edit_count", 0)) + 1
                if entry["edit_count"] >= 2 and (
                    state.get("last_tool_category") != "edit"
                    or state.get("last_edited_file") != rel_path
                ):
                    entry["rework_cycles"] = int(entry.get("rework_cycles", 0)) + 1
            elif cfg is not None and _is_non_test_code_source(rel_path):
                parts = Path(Path(rel_path).as_posix().lstrip("./")).parts
                if len(parts) >= 2:
                    top_dir = parts[0]
                    if top_dir not in NON_STACK_TOP_DIRS and top_dir not in IGNORE_DIRS:
                        state["unregistered_stack_edits"][top_dir] = (
                            int(state["unregistered_stack_edits"].get(top_dir, 0)) + 1
                        )

            if cfg is not None and _is_non_test_code_source(rel_path):
                target_file = ws_path / Path(rel_path).as_posix().lstrip("./")
                threshold = int((cfg.get("context_budget") or {}).get("max_source_file_lines", 800))
                if target_file.is_file():
                    try:
                        line_cnt = len(target_file.read_text(encoding="utf-8", errors="ignore").splitlines())
                        if line_cnt >= threshold:
                            state["oversized_file_edits"][rel_path] = line_cnt
                        else:
                            state["oversized_file_edits"].pop(rel_path, None)
                    except OSError:
                        pass

            state["last_edited_file"] = rel_path
        state["last_tool_category"] = "edit"
        save_telemetry(ws_path, state)
        return

    if tool_name in SHELL_TOOLS and cmd:
        lower_cmd = cmd.lower()
        if "git checkout" in lower_cmd or "git restore" in lower_cmd:
            for edited_rel, entry in state["layer_file_edits"].items():
                if edited_rel in cmd or Path(edited_rel).name in cmd:
                    entry["reverted"] = True

        norm_cmd = normalize_shell_command(cmd)
        if norm_cmd:
            if norm_cmd.lower().startswith(_get_poll_prefixes(ws_path)):
                state["poll_commands"][norm_cmd] = int(state["poll_commands"].get(norm_cmd, 0)) + 1

            history: list[str] = state["shell_history"]
            if not history or history[-1] != norm_cmd:
                history.append(norm_cmd)
                if len(history) > 30:
                    del history[:-30]
                if len(history) >= 3:
                    tri = history[-3:]
                    if len(set(tri)) == 3:
                        key = " -> ".join(tri)
                        state["command_ngrams"][key] = int(state["command_ngrams"].get(key, 0)) + 1

        state["last_tool_category"] = "shell"
        save_telemetry(ws_path, state)


def _has_adr_git_changes(ws_path: Path) -> bool:
    if not (ws_path / ".git").exists():
        return False
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", "docs/adr/"],
            cwd=str(ws_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(proc.stdout.strip())
    except Exception:
        return False


def evaluate_friction_opportunities(ws_path: Path) -> Optional[str]:
    """
    Evaluate 6 deterministic friction & opportunity thresholds at turn completion.
    Fires at most 1 nudge per turn (recording its cooldown key so it never nags twice).
    """
    state = load_telemetry(ws_path)
    state["last_tool_category"] = "stop"
    cooldowns: dict = state["cooldowns_fired"]
    now = time.time()
    cfg = _load_ai_native_config(ws_path)

    # Rule 1: Uncaptured Course-Correction (rework or revert in a layer file without ADR update)
    if (ws_path / ".ai-native.json").is_file() and not state["adr_or_agents_edited"] and not _has_adr_git_changes(ws_path):
        for rel_path, entry in sorted(state["layer_file_edits"].items()):
            rework = int(entry.get("rework_cycles", 0))
            edits = int(entry.get("edit_count", 0))
            reverted = bool(entry.get("reverted", False))
            cd_key = f"rework:{rel_path}"
            if (rework >= 1 or reverted or edits >= 3) and cd_key not in cooldowns:
                cooldowns[cd_key] = now
                save_telemetry(ws_path, state)
                matching_adrs = [a["rel_path"] for a in load_all_adrs(ws_path) if adr_matches_path(a, rel_path)]
                adr_hint = ", ".join(matching_adrs) if matching_adrs else "docs/adr/"
                return (
                    f"[ai-native-repo Friction Detector — Uncaptured Course-Correction] "
                    f"`{rel_path}` was reworked across multiple cycles (edits={edits}, rework={rework}, reverted={reverted}) "
                    f"without any update to its governing ADR (`{adr_hint}`). Before ending or in 1 line at the end of your reply, "
                    "record or offer to persist the `Context & Why` / `Anti-Patterns / Do Not Do` lesson so future agents do not repeat the mistake."
                )

    # Rule 2: Context Thrashing in an un-onboarded repo
    is_unonboarded = (
        not (ws_path / "AGENTS.md").exists()
        and not (ws_path / ".ai-native.json").exists()
        and (ws_path / ".git").exists()
    )
    if is_unonboarded and int(state.get("exploratory_reads_before_edit", 0)) >= 8 and "bootstrap_repo" not in cooldowns:
        cooldowns["bootstrap_repo"] = now
        save_telemetry(ws_path, state)
        cnt = state["exploratory_reads_before_edit"]
        return (
            f"[ai-native-repo Opportunity Detector — Context Thrashing] "
            f"You executed {cnt} exploratory read/search calls before editing in an un-onboarded repository. "
            "Append a 1-line offer to run `bootstrap_repo.py` so future tasks get 1-hop Context Capsules (`--for`), "
            "`Used By`/`Tested By` maps, and guardrail checks."
        )

    # Rule 3: Repeated 3-step command sequence
    for ngram, count in sorted(state["command_ngrams"].items()):
        cd_key = f"ngram:{ngram}"
        if int(count) >= 3 and cd_key not in cooldowns:
            cooldowns[cd_key] = now
            save_telemetry(ws_path, state)
            return (
                f"[ai-native-repo Opportunity Detector — Repeated Workflow] "
                f"The 3-step command workflow `{ngram}` ran {count} times. "
                "Append a 1-line offer at the end of your reply to package it into a reusable script or `.agents/skills/` skill."
            )

    # Rule 4: Recurring status / polling command
    for cmd_str, count in sorted(state["poll_commands"].items()):
        cd_key = f"poll:{cmd_str}"
        if int(count) >= 3 and cd_key not in cooldowns:
            cooldowns[cd_key] = now
            save_telemetry(ws_path, state)
            return (
                f"[ai-native-repo Opportunity Detector — Automation Candidate] "
                f"The status/polling command `{cmd_str}` ran {count} times. "
                "Append a 1-line offer at the end of your reply to turn it into a `/schedule` cron or GitHub Actions automation."
            )

    # Rule 5: Unregistered sub-project / new stack edited without .ai-native.json layers
    if cfg is not None:
        configured_prefixes = [
            str(layer.get("path") or "").strip().strip("/")
            for layer in cfg.get("layers", [])
            if isinstance(layer, dict) and layer.get("path")
        ]
        for top_dir in sorted(state["unregistered_stack_edits"]):
            cd_key = f"new_stack:{top_dir}"
            if cd_key in cooldowns:
                continue
            already_registered = any(
                lp == top_dir or lp.startswith(top_dir + "/") for lp in configured_prefixes
            )
            if not already_registered and _has_dir_stack_manifest_or_sources(ws_path / top_dir):
                cooldowns[cd_key] = now
                save_telemetry(ws_path, state)
                return (
                    f"[ai-native-repo Opportunity Detector — Unregistered Sub-Project `{top_dir}/`] "
                    f"Code under `{top_dir}/` is not yet registered in `.ai-native.json` layers. "
                    f"Before finishing (or in 1 line at the end of your reply), register `{top_dir}/` layers and `custom_gates` in `.ai-native.json`, "
                    f"add `{top_dir}/AGENTS.md` + a matching `docs/adr/*.md` (`applies_to: {top_dir}/*`), and add unit tests (`Tested By`)."
                )

    # Rule 6: Oversized source file (>= context_budget.max_source_file_lines, default 800)
    if cfg is not None:
        threshold = int((cfg.get("context_budget") or {}).get("max_source_file_lines", 800))
        for rel_path in sorted(state["oversized_file_edits"]):
            cd_key = f"monolith:{rel_path}"
            if cd_key in cooldowns:
                continue
            target_file = ws_path / Path(rel_path).as_posix().lstrip("./")
            if not target_file.is_file():
                continue
            try:
                current_lines = len(target_file.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                continue
            if current_lines >= threshold:
                cooldowns[cd_key] = now
                save_telemetry(ws_path, state)
                return (
                    f"[ai-native-repo Opportunity Detector — Oversized Source File] "
                    f"`{rel_path}` is {current_lines} lines (threshold: {threshold}), increasing agent context cost and edit blast radius. "
                    "Before finishing (or in 1 line at the end of your reply), consider splitting it into focused sub-modules (~200-400 lines)."
                )

    save_telemetry(ws_path, state)
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    for ws in extract_workspaces(payload):
        ws_path = Path(ws)
        if not ws_path.is_dir():
            continue
        record_tool_telemetry(ws_path, payload)
        auto_upgrade_repo_scripts(ws_path)
        if (ws_path / "AGENTS.md").exists():
            try:
                sync_workspace(ws_path)
            except Exception:
                pass

    # PostToolUse contract expects an empty JSON object on stdout
    print("{}")


if __name__ == "__main__":
    main()
