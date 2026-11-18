#!/usr/bin/env python3
"""
Deterministic AI-Native Repository Verifier (v3.0 — Zero-Collateral Verification Engine)
Enforces:
1. Python syntax compilation, LLM smells (bare except: pass, unimplemented stubs), and secret checks
2. Cross-module AST symbol & dataclass keyword argument resolution (anti-hallucination)
3. Declarative layer import boundaries from .ai-native.json (allowed/forbidden imports)
4. Anti-test-tampering & public dataclass contract regression checks vs git HEAD + blast-radius cap
5. Progressive disclosure context budgets on all AGENTS.md files
6. Fast deterministic unit tests (test_command)
7. Diff-scoped AST micro-mutation testing (when mutation_testing=true or --mutate)
"""
import argparse
import ast
import copy
import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".next", ".venv", "venv",
    ".idea", ".vscode", ".agents", ".githooks", "dist", "build"
}

SECRET_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z\-_]{35})\b")
]


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


def _is_abstract_method(node: ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        if (isinstance(dec, ast.Name) and dec.id == "abstractmethod") or (
            isinstance(dec, ast.Attribute) and dec.attr == "abstractmethod"
        ):
            return True
    return False


def check_llm_smells(py_path: Path, rel_path: str) -> list[str]:
    issues = []
    source = py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_path))
    is_test_or_verifier = (
        rel_path.startswith("tests/")
        or rel_path == "scripts/verify_architecture.py"
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass) and node.type is None:
                issues.append(
                    f"[LLM Smell] {rel_path}:{node.lineno} has a bare `except: pass` (catch specific exceptions or log)."
                )
        elif isinstance(node, ast.FunctionDef) and not _is_abstract_method(node) and not is_test_or_verifier:
            body_no_doc = [
                stmt for stmt in node.body
                if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str))
            ]
            if len(body_no_doc) == 1 and isinstance(body_no_doc[0], ast.Raise):
                exc = body_no_doc[0].exc
                exc_name = exc.func.id if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) else (
                    exc.id if isinstance(exc, ast.Name) else ""
                )
                if exc_name == "NotImplementedError":
                    issues.append(
                        f"[Unimplemented Stub] {rel_path}:{node.lineno} function `{node.name}` only raises NotImplementedError."
                    )

    if not is_test_or_verifier:
        for idx, line in enumerate(source.splitlines(), start=1):
            for pat in SECRET_PATTERNS:
                if pat.search(line):
                    issues.append(f"[Secret Leak Risk] {rel_path}:{idx} matches a hardcoded token pattern.")
    return issues


def matches_prefix(imp: str, prefix: str) -> bool:
    return imp == prefix or imp.startswith(prefix + ".")


def _is_dataclass_def(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "dataclass")
        or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
        for d in node.decorator_list
    )


def _extract_dataclass_fields(tree: ast.Module) -> dict[str, set[str]]:
    dc_map: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_dataclass_def(node):
            fields = {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
            dc_map[node.name] = fields
    return dc_map


def check_symbol_and_dataclass_resolution(workspace_root: Path) -> list[str]:
    issues: list[str] = []
    module_exports: dict[str, set[str]] = {}
    all_dataclasses: dict[str, set[str]] = {}
    parsed_files: list[tuple[str, ast.Module]] = []

    for py_file in sorted(workspace_root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in py_file.relative_to(workspace_root).parts):
            continue
        rel = py_file.relative_to(workspace_root).as_posix()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue
        parsed_files.append((rel, tree))

        if py_file.name == "__init__.py":
            mod_name = ".".join(py_file.relative_to(workspace_root).parent.parts)
        else:
            mod_name = ".".join(py_file.relative_to(workspace_root).with_suffix("").parts)

        exports: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                exports.add(node.name)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        exports.add(tgt.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                exports.add(node.target.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    exports.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    exports.add(alias.asname or alias.name)
        if mod_name:
            module_exports[mod_name] = exports

        dc_fields = _extract_dataclass_fields(tree)
        for cls_name, fields in dc_fields.items():
            all_dataclasses[f"{mod_name}.{cls_name}"] = fields

    for rel, tree in parsed_files:
        imported_dataclasses: dict[str, set[str]] = {}
        local_dc = _extract_dataclass_fields(tree)
        imported_dataclasses.update(local_dc)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and is_internal_module(node.module, workspace_root):
                if node.module in module_exports:
                    mod_dir = workspace_root.joinpath(*node.module.split("."))
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        is_submod = (mod_dir / alias.name).is_dir() or (mod_dir / f"{alias.name}.py").is_file()
                        if not is_submod and alias.name not in module_exports[node.module]:
                            issues.append(
                                f"[Hallucinated Import] {rel}:{node.lineno} imports `{alias.name}` from `{node.module}`, which is not defined there."
                            )
                        fq = f"{node.module}.{alias.name}"
                        if fq in all_dataclasses:
                            imported_dataclasses[alias.asname or alias.name] = all_dataclasses[fq]

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                cls_name = node.func.id
                if cls_name in imported_dataclasses:
                    valid_fields = imported_dataclasses[cls_name]
                    for kw in node.keywords:
                        if kw.arg is not None and kw.arg not in valid_fields:
                            issues.append(
                                f"[Hallucinated Dataclass Field] {rel}:{node.lineno} passes unknown keyword `{kw.arg}` to dataclass `{cls_name}`."
                            )
    return issues


def _has_git_head(workspace_root: Path) -> bool:
    if not (workspace_root / ".git").exists():
        return False
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _collect_test_info(tree: ast.Module) -> tuple[dict[str, bool], int]:
    tests: dict[str, bool] = {}
    assert_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            has_skip = False
            for dec in node.decorator_list:
                dec_txt = ast.unparse(dec).lower()
                if "skip" in dec_txt:
                    has_skip = True
            tests[node.name] = has_skip
        elif isinstance(node, ast.Assert):
            assert_count += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr.startswith("assert"):
                assert_count += 1
    return tests, assert_count


def check_git_head_regressions(workspace_root: Path, config: dict) -> list[str]:
    if not _has_git_head(workspace_root):
        return []
    issues: list[str] = []

    diff_proc = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "*.py"],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
    )
    changed_py = [line.strip() for line in diff_proc.stdout.splitlines() if line.strip()]

    for rel in changed_py:
        full_path = workspace_root / rel
        head_proc = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
        )
        if head_proc.returncode != 0:
            continue
        try:
            old_tree = ast.parse(head_proc.stdout)
        except Exception:
            continue

        is_test_file = rel.startswith("tests/") or Path(rel).name.startswith("test_")
        if is_test_file:
            if not full_path.exists():
                issues.append(f"[Test Tampering Blocked] Tracked test file `{rel}` was deleted.")
                continue
            try:
                new_tree = ast.parse(full_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            old_tests, old_asserts = _collect_test_info(old_tree)
            new_tests, new_asserts = _collect_test_info(new_tree)

            missing = sorted(set(old_tests) - set(new_tests))
            if missing:
                issues.append(
                    f"[Test Tampering Blocked] {rel} deleted existing test(s): {', '.join(missing)}."
                )
            for t_name, was_skipped in old_tests.items():
                if t_name in new_tests and not was_skipped and new_tests[t_name]:
                    issues.append(
                        f"[Test Tampering Blocked] {rel} added a skip decorator to existing test `{t_name}`."
                    )
            if new_asserts < old_asserts:
                issues.append(
                    f"[Test Tampering Blocked] {rel} reduced assertion count from {old_asserts} to {new_asserts}."
                )
        elif config.get("enforce_contract_compatibility", True) and not rel.startswith("scripts/"):
            if not full_path.exists():
                continue
            try:
                new_tree = ast.parse(full_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            old_dc = _extract_dataclass_fields(old_tree)
            new_dc = _extract_dataclass_fields(new_tree)
            for cls_name, old_fields in old_dc.items():
                if cls_name in new_dc:
                    removed = sorted(old_fields - new_dc[cls_name])
                    if removed:
                        issues.append(
                            f"[Collateral Contract Regression] {rel} removed field(s) {removed} from dataclass `{cls_name}`."
                        )

    max_layers = config.get("max_layers_touched")
    if max_layers is not None and config.get("layers"):
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
        )
        dirty_paths = []
        for line in status_proc.stdout.splitlines():
            if len(line) >= 4:
                p = line[3:].split(" -> ")[-1].strip()
                if p.endswith(".py"):
                    dirty_paths.append(p)
        touched_layers = set()
        for layer in config["layers"]:
            l_path = layer["path"].rstrip("/")
            if any(dp == l_path or dp.startswith(l_path + "/") for dp in dirty_paths):
                touched_layers.add(l_path)
        if len(touched_layers) > int(max_layers):
            issues.append(
                f"[Blast Radius Exceeded] Changes touch {len(touched_layers)} layers "
                f"({', '.join(sorted(touched_layers))}), exceeding max_layers_touched={max_layers}."
            )

    return issues


def _get_modified_lines_by_file(workspace_root: Path) -> dict[str, set[int]]:
    diff_proc = subprocess.run(
        ["git", "diff", "-U0", "HEAD", "--", "*.py"],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
    )
    changed: dict[str, set[int]] = {}
    current_file: Optional[str] = None
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            rel = line[6:].strip()
            if rel.startswith("tests/") or rel.startswith("scripts/") or Path(rel).name == "__init__.py":
                current_file = None
            else:
                current_file = rel
                changed.setdefault(current_file, set())
        elif current_file and line.startswith("@@ "):
            m = hunk_re.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                for ln in range(start, start + count):
                    changed[current_file].add(ln)
    return {k: v for k, v in changed.items() if v}


def run_diff_mutation_check(workspace_root: Path, test_cmd: str, max_mutants: int = 3) -> list[str]:
    if not test_cmd or not _has_git_head(workspace_root):
        return []
    modified_lines = _get_modified_lines_by_file(workspace_root)
    if not modified_lines:
        return []

    op_swaps = {
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
        ast.In: ast.NotIn,
        ast.NotIn: ast.In,
        ast.Is: ast.IsNot,
        ast.IsNot: ast.Is,
    }
    candidates: list[tuple[Path, str, int, int, int, str, str]] = []

    for rel, line_nums in sorted(modified_lines.items()):
        py_path = workspace_root / rel
        if not py_path.exists():
            continue
        source = py_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_path))
        except Exception:
            continue

        lines = source.splitlines(keepends=True)
        for node in ast.walk(tree):
            if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
                continue
            if node.lineno != node.end_lineno or node.lineno not in line_nums:
                continue
            if node.col_offset is None or node.end_col_offset is None:
                continue

            mutated_code = None
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                op_type = type(node.ops[0])
                if op_type in op_swaps:
                    mut_node = copy.deepcopy(node)
                    mut_node.ops[0] = op_swaps[op_type]()
                    mutated_code = ast.unparse(mut_node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
                mutated_code = "False" if node.value else "True"

            if mutated_code is None:
                continue

            raw_line_bytes = lines[node.lineno - 1].encode("utf-8")
            orig_seg = raw_line_bytes[node.col_offset:node.end_col_offset].decode("utf-8", errors="ignore")
            if orig_seg == mutated_code:
                continue

            # Preserve parenthesized safety for Compare expressions
            if isinstance(node, ast.Compare):
                mutated_code = f"({mutated_code})"

            candidates.append((
                py_path, rel, node.lineno, node.col_offset, node.end_col_offset, orig_seg, mutated_code
            ))
            if len(candidates) >= max_mutants:
                break
        if len(candidates) >= max_mutants:
            break

    issues: list[str] = []
    for py_path, rel, lineno, col_s, col_e, orig_seg, mutated_code in candidates:
        orig_text = py_path.read_text(encoding="utf-8")
        lines = orig_text.splitlines(keepends=True)
        line_bytes = lines[lineno - 1].encode("utf-8")
        mut_line = (line_bytes[:col_s] + mutated_code.encode("utf-8") + line_bytes[col_e:]).decode("utf-8")
        lines[lineno - 1] = mut_line
        mut_text = "".join(lines)

        try:
            ast.parse(mut_text)
        except Exception:
            continue

        try:
            py_path.write_text(mut_text, encoding="utf-8")
            proc = subprocess.run(
                test_cmd,
                shell=True,
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                issues.append(
                    f"[Weak Test / Surviving Mutant] {rel}:{lineno} — inverting `{orig_seg}` -> `{mutated_code}` did not fail any test."
                )
        finally:
            py_path.write_text(orig_text, encoding="utf-8")

    return issues


def verify_workspace(workspace_root: Path, run_tests: bool = True, force_mutate: bool = False) -> list[str]:
    errors: list[str] = []
    config_path = workspace_root / ".ai-native.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"[Config Error] Invalid .ai-native.json: {exc}")
            return errors

    # 1. Syntax, LLM Smells, Stubs & Secrets Check
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

    # 2. Cross-Module AST Symbol & Dataclass Field Resolution
    errors.extend(check_symbol_and_dataclass_resolution(workspace_root))

    # 3. Layer Import Boundary Check
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

    # 4. Anti-Test-Tampering, Public Contract Regression & Blast-Radius Check vs HEAD
    errors.extend(check_git_head_regressions(workspace_root, config))

    # 5. Progressive Disclosure Context Budget Check
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

    # 6. Fast Deterministic Unit Tests
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

    # 7. Diff-Scoped AST Micro-Mutation Testing
    if not errors and run_tests and test_cmd and (force_mutate or config.get("mutation_testing", False)):
        errors.extend(run_diff_mutation_check(workspace_root, test_cmd))

    return errors


def main():
    parser = argparse.ArgumentParser(description="Verify AI-native repository boundaries, regressions, budgets, and tests.")
    parser.add_argument("--workspace", default=".", help="Workspace root directory")
    parser.add_argument("--no-tests", action="store_true", help="Skip running test_command")
    parser.add_argument("--mutate", action="store_true", help="Force diff-scoped AST micro-mutation testing")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    errs = verify_workspace(root, run_tests=not args.no_tests, force_mutate=args.mutate)
    if errs:
        print("[ai-native-repo] Verification FAILED:")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("[ai-native-repo] All architecture boundaries, contracts, context budgets, and tests PASSED.")


if __name__ == "__main__":
    main()
