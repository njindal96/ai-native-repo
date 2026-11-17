#!/usr/bin/env python3
"""
Deterministic AI-Native Repository Verifier (v2.0)
Enforces:
1. Python syntax compilation across all modules
2. Declarative layer import boundaries from .ai-native.json (allowed/forbidden imports)
3. LLM code-smell guardrails (no silent bare `except: pass`)
4. Progressive disclosure context budgets on all AGENTS.md files
5. Fast offline unit tests (if configured in .ai-native.json)
"""
import argparse
import ast
import json
import py_compile
import subprocess
import sys
from pathlib import Path

IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".next", ".venv", "venv",
    ".idea", ".vscode", ".agents", ".githooks", "dist", "build"
}


def extract_imports(py_path: Path) -> list[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def is_internal_module(imp: str, repo_root: Path) -> bool:
    top = imp.split(".")[0]
    if not top or top in {"tests", "scripts", "docs"}:
        return False
    return (repo_root / top).is_dir() or (repo_root / f"{top}.py").is_file()


def check_llm_smells(py_path: Path, rel_path: str) -> list[str]:
    issues = []
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass) and node.type is None:
                issues.append(
                    f"[LLM Smell] {rel_path}:{node.lineno} has a bare `except: pass` (catch specific exceptions or log)."
                )
    return issues


def matches_prefix(imp: str, prefix: str) -> bool:
    return imp == prefix or imp.startswith(prefix + ".")


def verify_workspace(workspace_root: Path, run_tests: bool = True) -> list[str]:
    errors: list[str] = []
    config_path = workspace_root / ".ai-native.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"[Config Error] Invalid .ai-native.json: {exc}")
            return errors

    # 1. Syntax & LLM Smell Check
    for py_file in sorted(workspace_root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in py_file.relative_to(workspace_root).parts):
            continue
        rel = py_file.relative_to(workspace_root).as_posix()
        try:
            py_compile.compile(str(py_file), doraise=True)
            errors.extend(check_llm_smells(py_file, rel))
        except Exception as exc:
            errors.append(f"[Syntax Error] {rel}: {exc}")

    if errors:
        return errors

    # 2. Layer Import Boundary Check
    layers = config.get("layers", [])
    for layer in layers:
        layer_path = workspace_root / layer["path"]
        if not layer_path.exists():
            continue
        forbidden = layer.get("forbidden_imports", [])
        allowed_prefixes = layer.get("allowed_internal_prefixes")
        py_targets = [layer_path] if layer_path.is_file() else sorted(layer_path.rglob("*.py"))

        for pf in py_targets:
            if pf.name == "__init__.py":
                continue
            rel = pf.relative_to(workspace_root).as_posix()
            for imp in extract_imports(pf):
                for bad in forbidden:
                    if matches_prefix(imp, bad):
                        errors.append(
                            f"[Boundary Violation] {rel} imports `{imp}`, which is forbidden in layer `{layer['path']}` (rule: {layer.get('reason', 'layer isolation')})."
                        )
                if allowed_prefixes is not None and is_internal_module(imp, workspace_root):
                    if not any(matches_prefix(imp, ok) for ok in allowed_prefixes):
                        errors.append(
                            f"[Boundary Violation] {rel} imports internal module `{imp}`, which is outside allowed prefixes {allowed_prefixes} for `{layer['path']}`."
                        )

    # 3. Progressive Disclosure Context Budget Check
    budget = config.get("context_budget", {"max_lines": 200, "max_bytes": 20000})
    max_lines = budget.get("max_lines", 200)
    max_bytes = budget.get("max_bytes", 20000)
    for af in sorted(workspace_root.rglob("AGENTS.md")):
        if any(part in IGNORE_DIRS for part in af.relative_to(workspace_root).parts):
            continue
        rel = af.relative_to(workspace_root).as_posix()
        raw = af.read_text(encoding="utf-8")
        line_cnt = len(raw.splitlines())
        byte_cnt = len(raw.encode("utf-8"))
        if line_cnt > max_lines or byte_cnt > max_bytes:
            errors.append(
                f"[Context Budget Exceeded] {rel} has {line_cnt} lines / {byte_cnt} bytes "
                f"(cap: {max_lines} lines / {max_bytes} bytes). Move deep runbooks into .agents/skills/ or docs/adr/."
            )

    # 4. Fast Deterministic Unit Tests
    test_cmd = config.get("test_command")
    if run_tests and test_cmd:
        proc = subprocess.run(
            test_cmd,
            shell=True,
            cwd=str(workspace_root),
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            out = (proc.stdout + "\n" + proc.stderr).strip()[-1000:]
            errors.append(f"[Unit Test Failure] `{test_cmd}` failed:\n{out}")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Verify AI-native repository boundaries, budgets, and tests.")
    parser.add_argument("--workspace", default=".", help="Workspace root directory")
    parser.add_argument("--no-tests", action="store_true", help="Skip running test_command")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    errs = verify_workspace(root, run_tests=not args.no_tests)
    if errs:
        print("[ai-native-repo] Verification FAILED:")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("[ai-native-repo] All architecture boundaries, context budgets, and unit tests PASSED.")


if __name__ == "__main__":
    main()
