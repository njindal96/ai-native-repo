#!/usr/bin/env python3
"""
Automated 1-Command Repository Bootstrapper for AI-Native Repositories (v3.1).
Scaffolds cleanly and non-destructively:
1. Copies sync_context.py and verify_architecture.py into scripts/
2. Creates .ai-native.json (auto-detecting Python & polyglot layers + test command)
3. Initializes root AGENTS.md and scoped */AGENTS.md files, merging any pre-existing CLAUDE.md
4. Creates CLAUDE.md -> AGENTS.md symlink (with Windows-safe copy fallback on OSError)
5. Scaffolds starter docs/adr/001-ai-native-architecture.md if missing
6. Preserves & chains pre-existing .git/hooks/* or custom core.hooksPath hooks to .githooks/*.local
7. Supports non-destructive JSON hook merging via merge_hooks_into_settings()
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from sync_context import (  # noqa: E402
    END_MARKER,
    IGNORE_DIRS,
    POLYGLOT_EXTENSIONS,
    START_MARKER,
    sync_workspace,
)
from verify_architecture import verify_workspace  # noqa: E402

PLUGIN_SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT_DIR = PLUGIN_SCRIPTS_DIR.parent
ALL_CODE_EXTENSIONS = {".py"} | POLYGLOT_EXTENSIONS


def make_executable(path: Path):
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _has_source_files(directory: Path) -> bool:
    for p in directory.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALL_CODE_EXTENSIONS:
            if not any(part in IGNORE_DIRS for part in p.relative_to(directory).parts):
                return True
    return False


def detect_code_layers(repo_root: Path) -> list[dict]:
    layers = []
    candidates = []
    skip_top = IGNORE_DIRS | {"scripts", "tests", "docs", ".github", "skills", "rules", "assets"}
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir() or child.name in skip_top:
            continue
        subdirs = [
            d for d in sorted(child.iterdir())
            if d.is_dir() and d.name not in IGNORE_DIRS and _has_source_files(d)
        ]
        if subdirs:
            candidates.extend(subdirs)
        elif _has_source_files(child):
            candidates.append(child)

    for c in candidates:
        rel = c.relative_to(repo_root).as_posix()
        layers.append({
            "path": rel,
            "forbidden_imports": [],
            "reason": f"Layer boundary for {rel}",
        })
    return layers


def detect_test_command(repo_root: Path) -> str:
    if (repo_root / "tests").is_dir() and any((repo_root / "tests").rglob("test_*.py")):
        return "python3 -m unittest discover -s tests -p 'test_*.py'"
    if (repo_root / "package.json").is_file():
        return "npm test --if-present"
    if (repo_root / "go.mod").is_file():
        return "go test ./..."
    if (repo_root / "Cargo.toml").is_file():
        return "cargo test"
    if (repo_root / "pom.xml").is_file():
        return "mvn test"
    if (repo_root / "gradlew").is_file():
        return "./gradlew test"
    return ""


def ensure_markers_in_agents_md(agents_path: Path, title: str, is_root: bool) -> bool:
    if not agents_path.exists():
        if is_root:
            body = (
                f"# AGENTS.md — {title}\n\n"
                "> **For Scoped AI Agents:** Read this file first for global architecture boundaries, "
                "then read the scoped `AGENTS.md` inside the specific sub-directory you are modifying.\n\n"
                "## 1. Verification & Operational Tiers\n"
                "- **Verify Architecture & Tests:** `python3 scripts/verify_architecture.py`\n"
                "- **Sync Contracts & ADRs:** `python3 scripts/sync_context.py`\n"
                "- **Single-Hop Context Capsule (Why, Callers & Tests):** `python3 scripts/sync_context.py --for <file>`\n"
                "- **Always:** Read the target directory's `AGENTS.md` and governing `docs/adr/*.md` first; run `python3 scripts/verify_architecture.py` after edits.\n"
                "- **Ask First:** Changing database schemas, public contracts, or adding external dependencies.\n"
                "- **Never:** Cross forbidden layer imports in `.ai-native.json` or hand-edit inside `<!-- AUTO-GENERATED -->` blocks.\n\n"
                f"{START_MARKER}\n{END_MARKER}\n"
            )
        else:
            body = (
                f"# AGENTS.md — `{title}/`\n\n"
                "## Purpose & Invariants\n"
                f"- Scoped context and live code contract for `{title}/`.\n\n"
                "## Action Tiers (`Always` / `Ask First` / `Never`)\n"
                "- **Always:** Keep functions/classes typed and update semantic notes here when changing contracts.\n"
                "- **Ask First:** Renaming or removing public interfaces used by other layers.\n"
                "- **Never:** Hand-edit inside `<!-- AUTO-GENERATED -->` blocks.\n\n"
                f"{START_MARKER}\n{END_MARKER}\n"
            )
        agents_path.write_text(body, encoding="utf-8")
        return True

    text = agents_path.read_text(encoding="utf-8")
    start_m = re.search(rf"^[ \t]*{re.escape(START_MARKER)}[ \t]*$", text, re.M)
    end_m = re.search(rf"^[ \t]*{re.escape(END_MARKER)}[ \t]*$", text, re.M)
    if not start_m or not end_m:
        text = text.rstrip() + f"\n\n{START_MARKER}\n{END_MARKER}\n"
        agents_path.write_text(text, encoding="utf-8")
        return True
    return False


def _merge_claude_md_into_agents_md(repo_root: Path) -> Optional[str]:
    claude_md = repo_root / "CLAUDE.md"
    agents_md = repo_root / "AGENTS.md"
    merged_note: Optional[str] = None

    if claude_md.exists() and not claude_md.is_symlink():
        existing_claude = claude_md.read_text(encoding="utf-8").strip()
        agents_text = agents_md.read_text(encoding="utf-8")
        if existing_claude and existing_claude not in agents_text:
            section = f"## Project Context (Merged from CLAUDE.md)\n\n{existing_claude}\n\n"
            start_m = re.search(rf"^[ \t]*{re.escape(START_MARKER)}[ \t]*$", agents_text, re.M)
            if start_m:
                updated = agents_text[:start_m.start()] + section + agents_text[start_m.start():]
            else:
                updated = agents_text.rstrip() + "\n\n" + section
            agents_md.write_text(updated, encoding="utf-8")
            merged_note = "Merged existing CLAUDE.md into AGENTS.md"
        claude_md.unlink()

    if not claude_md.exists() and not claude_md.is_symlink():
        try:
            claude_md.symlink_to("AGENTS.md")
            return merged_note or "Created CLAUDE.md -> AGENTS.md symlink"
        except OSError:
            shutil.copy2(agents_md, claude_md)
            return merged_note or "Copied AGENTS.md to CLAUDE.md (symlink unsupported fallback)"
    return merged_note


def _install_git_hooks_non_destructive(repo_root: Path) -> list[str]:
    actions: list[str] = []
    githooks_dir = repo_root / ".githooks"
    githooks_dir.mkdir(parents=True, exist_ok=True)
    has_git = (repo_root / ".git").exists()

    old_hooks_dir: Optional[Path] = None
    if has_git:
        proc = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        raw_hp = proc.stdout.strip()
        if proc.returncode == 0 and raw_hp and raw_hp.rstrip("/") not in (".githooks", "./.githooks"):
            cand = Path(raw_hp)
            old_hooks_dir = cand if cand.is_absolute() else (repo_root / cand)

    for hook_name, body_lines in (
        (
            "pre-commit",
            [
                "python3 scripts/sync_context.py",
                "python3 scripts/verify_architecture.py",
                "git add AGENTS.md */AGENTS.md **/AGENTS.md 2>/dev/null || true",
            ],
        ),
        (
            "pre-push",
            [
                "python3 scripts/verify_architecture.py",
            ],
        ),
    ):
        target_hook = githooks_dir / hook_name
        local_hook = githooks_dir / f"{hook_name}.local"

        if not local_hook.exists():
            candidates = []
            if old_hooks_dir is not None:
                candidates.append(old_hooks_dir / hook_name)
            if has_git:
                candidates.append(repo_root / ".git" / "hooks" / hook_name)
            if target_hook.exists():
                candidates.append(target_hook)

            for src_hook in candidates:
                if src_hook.is_file():
                    try:
                        text = src_hook.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        text = ""
                    if "verify_architecture.py" not in text:
                        shutil.copy2(src_hook, local_hook)
                        make_executable(local_hook)
                        actions.append(f"Preserved existing {hook_name} hook to .githooks/{hook_name}.local")
                        if src_hook == target_hook:
                            target_hook.unlink()
                        break

        if not target_hook.exists():
            hook_script = (
                "#!/usr/bin/env sh\n"
                "set -e\n"
                f'HOOK_LOCAL="$(dirname "$0")/{hook_name}.local"\n'
                'if [ -x "$HOOK_LOCAL" ]; then\n'
                '  "$HOOK_LOCAL" "$@"\n'
                "fi\n"
                + "\n".join(body_lines)
                + "\n"
            )
            target_hook.write_text(hook_script, encoding="utf-8")
            make_executable(target_hook)
            actions.append(f"Created .githooks/{hook_name}")

    if has_git:
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=str(repo_root), check=False)

    return actions


def merge_hooks_into_settings(settings_path: Path, plugin_dir: Optional[Path] = None) -> bool:
    """
    Non-destructively merge ai-native-repo hooks into an existing JSON settings file
    (e.g., Claude Code ~/.claude/settings.json or Antigravity hooks config) without
    overwriting pre-existing keys or user hooks.
    """
    p_dir = (plugin_dir or PLUGIN_ROOT_DIR).resolve()
    data: dict = {}
    if settings_path.exists():
        raw = settings_path.read_text(encoding="utf-8").strip()
        if raw:
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError(f"{settings_path} must contain a top-level JSON object.")
            data = loaded

    hooks_section = data.setdefault("hooks", {})
    if not isinstance(hooks_section, dict):
        raise ValueError(f"{settings_path} 'hooks' field must be a JSON object.")

    desired = {
        "PreToolUse": {
            "matcher": "run_command|Bash|run_shell_command|bash|shell|execute_command|terminal|read_file|view_file|Read|edit_file|write_file|Edit|Write|MultiEdit",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python3 {p_dir / 'scripts' / 'pre_tool_hook.py'}",
                    "timeout": 120,
                }
            ],
        },
        "PostToolUse": {
            "matcher": "write_file|edit_file|run_command|read_file|view_file|grep_search|find_by_name|Write|Edit|MultiEdit|Bash|run_shell_command|Read|Grep|Glob",
            "hooks": [
                {
                    "type": "command",
                    "command": f"python3 {p_dir / 'scripts' / 'post_tool_hook.py'}",
                    "timeout": 15,
                }
            ],
        },
        "Stop": {
            "type": "command",
            "command": f"python3 {p_dir / 'scripts' / 'stop_gate_hook.py'}",
            "timeout": 120,
        },
    }

    changed = False
    for event, entry in desired.items():
        lst = hooks_section.setdefault(event, [])
        if not isinstance(lst, list):
            continue
        target_cmd = (
            entry["command"]
            if "command" in entry
            else entry["hooks"][0]["command"]
        )
        serialized = json.dumps(lst)
        if os.path.basename(target_cmd.split()[-1]) not in serialized:
            lst.append(entry)
            changed = True

    if changed or not settings_path.exists():
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


def _ensure_adr_rationale_sections(adr_dir: Path, repo_root: Path) -> list[str]:
    actions: list[str] = []
    why_pat = re.compile(r"^##+\s+.*\b(context|why|rationale|background)\b", re.I | re.M)
    guard_pat = re.compile(r"^##+\s+.*\b(decision|invariants?|anti-patterns?|do\s+not|guardrails?)\b", re.I | re.M)
    for adr_path in sorted(adr_dir.glob("*.md")):
        if adr_path.name.lower() == "readme.md":
            continue
        raw = adr_path.read_text(encoding="utf-8")
        norm = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        body = norm
        open_m = re.match(r"^---[ \t]*\n", norm)
        if open_m:
            rest = norm[open_m.end():]
            close_m = re.search(r"(?:^|\n)---[ \t]*(?:\n|$)", rest)
            if close_m:
                body = rest[close_m.end():]
        additions: list[str] = []
        if not why_pat.search(body):
            additions.append("## Context & Why\nDescribe why this architectural constraint or design exists.")
        if not guard_pat.search(body):
            additions.append(
                "## Decision & Invariants\nDescribe non-negotiable final-state invariants and anti-patterns (`Do Not Do`)."
            )
        if additions:
            updated = raw.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
            adr_path.write_text(updated, encoding="utf-8")
            actions.append(f"Upgraded {adr_path.relative_to(repo_root).as_posix()} rationale sections")
    return actions


def bootstrap_repository(repo_root: Path) -> list[str]:
    actions = []
    repo_root = repo_root.resolve()

    # 1. Copy/upgrade sync_context.py and verify_architecture.py into repo/scripts/
    target_scripts = repo_root / "scripts"
    target_scripts.mkdir(parents=True, exist_ok=True)
    for script_name in ("sync_context.py", "verify_architecture.py"):
        src = PLUGIN_SCRIPTS_DIR / script_name
        dst = target_scripts / script_name
        if src.resolve() != dst.resolve():
            existed = dst.exists()
            if not existed or src.read_bytes() != dst.read_bytes():
                shutil.copy2(src, dst)
                make_executable(dst)
                actions.append(f"{'Updated' if existed else 'Installed'} scripts/{script_name}")

    # 2. Create or non-destructively upgrade .ai-native.json
    config_path = repo_root / ".ai-native.json"
    layers = detect_code_layers(repo_root)
    if not config_path.exists():
        cfg = {
            "version": "3.0",
            "context_budget": {"max_lines": 200, "max_bytes": 20000},
            "test_command": detect_test_command(repo_root),
            "enforce_contract_compatibility": True,
            "enforce_pre_edit_context": True,
            "enforce_security_checks": True,
            "mutation_testing": True,
            "max_mutants": 25,
            "layers": layers,
        }
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        actions.append("Created .ai-native.json")
    else:
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            layers = cfg.get("layers", layers)
            changed_cfg = False
            if "enforce_pre_edit_context" not in cfg:
                cfg["enforce_pre_edit_context"] = True
                changed_cfg = True
            if "enforce_security_checks" not in cfg:
                cfg["enforce_security_checks"] = True
                changed_cfg = True
            if "max_mutants" not in cfg:
                cfg["max_mutants"] = 25
                changed_cfg = True
            if changed_cfg:
                config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
                actions.append("Upgraded .ai-native.json defaults")
        except Exception:
            pass

    # 3. Ensure root AGENTS.md and layer AGENTS.md files exist with markers
    if ensure_markers_in_agents_md(repo_root / "AGENTS.md", repo_root.name, is_root=True):
        actions.append("Initialized root AGENTS.md")

    for layer in layers:
        l_path = repo_root / layer["path"]
        if l_path.is_dir():
            if ensure_markers_in_agents_md(l_path / "AGENTS.md", layer["path"], is_root=False):
                actions.append(f"Initialized {layer['path']}/AGENTS.md")

    # 4. Merge any existing CLAUDE.md and link CLAUDE.md -> AGENTS.md safely
    claude_action = _merge_claude_md_into_agents_md(repo_root)
    if claude_action:
        actions.append(claude_action)

    # 5. Ensure docs/adr/ exists with at least one starter ADR if missing
    adr_dir = repo_root / "docs" / "adr"
    if not adr_dir.exists():
        adr_dir.mkdir(parents=True, exist_ok=True)
        starter_adr = adr_dir / "001-ai-native-architecture.md"
        starter_adr.write_text(
            "---\n"
            "id: ADR-001\n"
            "title: AI-Native Architecture & Layer Boundaries\n"
            "status: accepted\n"
            "applies_to: *\n"
            "summary: Enforce hierarchical AGENTS.md contracts, AST boundary checks via .ai-native.json, and pre-commit auto-sync.\n"
            "---\n\n"
            "# ADR-001: AI-Native Architecture & Layer Boundaries\n\n"
            "## Context & Why\n"
            "Autonomous coding agents degrade when architectural intent, layer isolation rules, and domain invariants "
            "live in scattered docs. Placing machine-readable ADRs and `.ai-native.json` layer boundaries next to the "
            "code keeps agent modifications aligned with original design intent.\n\n"
            "## Decision & Invariants\n"
            "- All modules follow `.ai-native.json` layer boundaries and auto-sync `AGENTS.md` contracts via `scripts/sync_context.py`.\n"
            "- Run `python3 scripts/sync_context.py --for <file>` before modifying unfamiliar modules.\n\n"
            "## Anti-Patterns / Do Not Do\n"
            "- Never cross `forbidden_imports` boundaries or silently bypass verification gates.\n",
            encoding="utf-8",
        )
        actions.append("Created docs/adr/001-ai-native-architecture.md")
    else:
        actions.extend(_ensure_adr_rationale_sections(adr_dir, repo_root))

    # 6. Install .githooks/pre-commit & pre-push while preserving pre-existing hooks
    actions.extend(_install_git_hooks_non_destructive(repo_root))

    # 7. Sync all AGENTS.md files
    sync_workspace(repo_root)
    return actions


def main():
    parser = argparse.ArgumentParser(description="Bootstrap a repository with AI-Native v3.1 structure and hooks.")
    parser.add_argument("--workspace", default=".", help="Target repository root")
    parser.add_argument("--merge-settings", default="", help="Optional JSON settings file to merge plugin hooks into")
    args = parser.parse_args()

    if args.merge_settings:
        settings_path = Path(args.merge_settings).expanduser().resolve()
        merged = merge_hooks_into_settings(settings_path)
        print(f"[ai-native-repo] {'Merged' if merged else 'Hooks already present in'} {settings_path}")
        return

    target = Path(args.workspace).resolve()
    actions = bootstrap_repository(target)
    if actions:
        print(f"[ai-native-repo] Bootstrapped {target.name}: " + ", ".join(actions))
    else:
        print(f"[ai-native-repo] {target.name} is already bootstrapped; synced contracts.")
    errs = verify_workspace(target, run_tests=True)
    if errs:
        print("[ai-native-repo] Verification warnings:")
        for e in errs:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
