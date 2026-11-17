#!/usr/bin/env python3
"""
Automated 1-Command Repository Bootstrapper for AI-Native Repositories.
Scaffolds (non-destructively):
1. .ai-native.json (auto-detecting layers and test commands if not present)
2. Root AGENTS.md + scoped sub-directory AGENTS.md files (with AUTO-GENERATED markers)
3. CLAUDE.md -> AGENTS.md symlink
4. docs/adr/001-ai-native-architecture.md (if docs/adr/ does not exist)
5. scripts/sync_context.py and scripts/verify_architecture.py
6. .githooks/pre-commit + git config core.hooksPath .githooks
7. Runs initial sync_context.py and verify_architecture.py
"""
import argparse
import json
import re
import shutil
import stat
import subprocess
from pathlib import Path
from sync_context import END_MARKER, IGNORE_DIRS, START_MARKER, sync_workspace
from verify_architecture import verify_workspace

PLUGIN_SCRIPTS_DIR = Path(__file__).resolve().parent


def make_executable(path: Path):
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def detect_code_layers(repo_root: Path) -> list[dict]:
    layers = []
    candidates = []
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir() or child.name in IGNORE_DIRS or child.name in {
            "scripts", "tests", "docs", ".github", "skills", "rules", "assets"
        }:
            continue
        subdirs = [
            d for d in sorted(child.iterdir())
            if d.is_dir() and d.name not in IGNORE_DIRS and (any(d.rglob("*.py")) or any(d.rglob("*.ts*")))
        ]
        if subdirs:
            candidates.extend(subdirs)
        elif any(child.rglob("*.py")) or any(child.rglob("*.ts*")):
            candidates.append(child)

    for c in candidates:
        rel = c.relative_to(repo_root).as_posix()
        layers.append({
            "path": rel,
            "forbidden_imports": [],
            "reason": f"Layer boundary for {rel}"
        })
    return layers


def detect_test_command(repo_root: Path) -> str:
    if (repo_root / "tests").is_dir() and any((repo_root / "tests").rglob("test_*.py")):
        return "python3 -m unittest discover -s tests -p 'test_*.py'"
    if (repo_root / "package.json").is_file():
        return "npm test --if-present"
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


def bootstrap_repository(repo_root: Path) -> list[str]:
    actions = []
    repo_root = repo_root.resolve()

    # 1. Copy sync_context.py and verify_architecture.py into repo/scripts/
    target_scripts = repo_root / "scripts"
    target_scripts.mkdir(parents=True, exist_ok=True)
    for script_name in ("sync_context.py", "verify_architecture.py"):
        src = PLUGIN_SCRIPTS_DIR / script_name
        dst = target_scripts / script_name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
            make_executable(dst)
            actions.append(f"Installed scripts/{script_name}")

    # 2. Create .ai-native.json if missing
    config_path = repo_root / ".ai-native.json"
    layers = detect_code_layers(repo_root)
    if not config_path.exists():
        cfg = {
            "version": "2.0",
            "context_budget": {"max_lines": 200, "max_bytes": 20000},
            "test_command": detect_test_command(repo_root),
            "layers": layers,
        }
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        actions.append("Created .ai-native.json")
    else:
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            layers = cfg.get("layers", layers)
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

    # 4. Ensure CLAUDE.md -> AGENTS.md symlink
    claude_md = repo_root / "CLAUDE.md"
    if not claude_md.exists() and not claude_md.is_symlink():
        claude_md.symlink_to("AGENTS.md")
        actions.append("Created CLAUDE.md -> AGENTS.md symlink")

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
            "All modules follow `.ai-native.json` layer boundaries and auto-sync `AGENTS.md` contracts via `scripts/sync_context.py`.\n",
            encoding="utf-8",
        )
        actions.append("Created docs/adr/001-ai-native-architecture.md")

    # 6. Install .githooks/pre-commit and configure git hooksPath
    githooks_dir = repo_root / ".githooks"
    githooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit = githooks_dir / "pre-commit"
    if not pre_commit.exists():
        pre_commit.write_text(
            "#!/usr/bin/env sh\n"
            "set -e\n"
            "python3 scripts/sync_context.py\n"
            "python3 scripts/verify_architecture.py\n"
            "git add AGENTS.md */AGENTS.md **/AGENTS.md 2>/dev/null || true\n",
            encoding="utf-8",
        )
        make_executable(pre_commit)
        actions.append("Created .githooks/pre-commit")

    if (repo_root / ".git").exists():
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=str(repo_root), check=False)

    # 7. Sync all AGENTS.md files
    sync_workspace(repo_root)
    return actions


def main():
    parser = argparse.ArgumentParser(description="Bootstrap a repository with AI-Native v2.0 structure and hooks.")
    parser.add_argument("--workspace", default=".", help="Target repository root")
    args = parser.parse_args()
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
