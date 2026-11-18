#!/usr/bin/env python3
"""
Stop Gate Verification & Semantic Drift Guard (v3.1)
Supports Antigravity, Claude Code, Cursor, Codex, and Gemini CLI Stop payloads.
Enforces:
1. Safe re-entry escape hatch (`stop_hook_active` / `continuationActive`) to prevent infinite refusal loops.
2. Full verification via verify_architecture.verify_workspace().
3. Public-contract-only Semantic Drift Gate with whitespace normalization (blocks space/blank-line bypasses
   and avoids false positives when only private helper bodies change), naming the exact file & symbols.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from post_tool_hook import evaluate_friction_opportunities  # noqa: E402
from sync_context import END_MARKER, START_MARKER, sync_workspace  # noqa: E402
from verify_architecture import verify_workspace  # noqa: E402


def split_semantic_and_auto(text: str) -> tuple[str, str]:
    start_m = re.search(rf"^[ \t]*{re.escape(START_MARKER)}[ \t]*$", text, re.M)
    end_m = re.search(rf"^[ \t]*{re.escape(END_MARKER)}[ \t]*$", text, re.M)
    if not start_m or not end_m or end_m.start() <= start_m.end():
        return text.strip(), ""
    before = text[:start_m.start()]
    auto_body = text[start_m.end():end_m.start()]
    after = text[end_m.end():]
    return (before + "\n" + after).strip(), auto_body.strip()


def normalize_semantic_prose(text: str) -> str:
    """Collapse all whitespace sequences so cosmetic spacing/blank-line edits cannot bypass semantic drift."""
    return re.sub(r"\s+", " ", text).strip()


def extract_public_contract_lines(auto_body: str) -> set[str]:
    """Extract only public symbol/import/export contract entries, ignoring formatting/module boilerplate."""
    contracts: set[str] = set()
    for line in auto_body.splitlines():
        s = line.strip()
        if (
            s.startswith("- **Dataclass ")
            or s.startswith("- **Class ")
            or s.startswith("- **Function:**")
            or s.startswith("- **Constants:**")
            or s.startswith("- **Exports:**")
            or s.startswith("- **Internal Imports:**")
            or s.startswith("- Fields:")
            or s.startswith("- Method:")
        ):
            contracts.add(s)
    return contracts


def check_semantic_drift(ws_path: Path) -> list[str]:
    """
    Compare public code contracts in each tracked layer AGENTS.md vs git HEAD.
    Requires non-whitespace semantic prose updates only when public contracts actually changed.
    Returns descriptive entries naming exact AGENTS.md paths and changed contract symbols.
    """
    drifted: list[str] = []
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
            old_contracts = extract_public_contract_lines(old_auto)
            new_contracts = extract_public_contract_lines(new_auto)
            if old_contracts != new_contracts:
                if normalize_semantic_prose(old_sem) == normalize_semantic_prose(new_sem):
                    changed_syms = sorted(old_contracts ^ new_contracts)
                    preview = "; ".join(changed_syms[:3])
                    drifted.append(f"{rel_path} (changed contract: {preview})" if preview else rel_path)
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
    if not isinstance(payload, dict):
        payload = {}

    has_termination_reason = "terminationReason" in payload
    is_claude_code = not has_termination_reason

    if payload.get("stop_hook_active") or payload.get("continuationActive"):
        print(format_response(is_claude_code, True))
        return
    if has_termination_reason and payload.get("terminationReason") != "NO_TOOL_CALL":
        print(format_response(False, True))
        return

    for ws in extract_workspaces(payload):
        ws_path = Path(ws)
        if not ws_path.is_dir():
            continue

        if (ws_path / "AGENTS.md").exists():
            # 1. Sync all AGENTS.md auto-generated blocks
            try:
                sync_workspace(ws_path)
            except Exception:
                pass

            # 2. Verify syntax, hallucinations, layer boundaries, tampering, budgets, tests & mutations
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
                        "but the semantic prose outside the <!-- AUTO-GENERATED --> block was not meaningfully updated. "
                        "Please document the contract change in those AGENTS.md file(s) before finishing."
                    ),
                ))
                return

        # 4. Threshold-gated Silent Friction & Opportunity Detector (any repository)
        try:
            opportunity = evaluate_friction_opportunities(ws_path)
        except Exception:
            opportunity = None
        if opportunity:
            print(format_response(is_claude_code, False, opportunity))
            return

    print(format_response(is_claude_code, True))


if __name__ == "__main__":
    main()
