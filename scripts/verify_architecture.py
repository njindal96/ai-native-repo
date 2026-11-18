#!/usr/bin/env python3
"""
Deterministic AI-Native Repository Verifier (v3.1 — Zero-Collateral Verification Engine)
Enforces:
1. Python syntax compilation, LLM smells (bare except: pass, unimplemented stubs), and secret checks
2. Cross-module AST symbol, module-alias & inherited @dataclass field resolution (anti-hallucination)
3. Declarative layer import boundaries from .ai-native.json (Python + TS/JS/Go/Rust/Java lexical imports)
4. Anti-test-tampering, public dataclass contract regression, .ai-native.json/.githooks tampering & blast-radius checks vs HEAD
5. Progressive disclosure context budgets on all AGENTS.md files
6. Fast deterministic unit tests (test_command) with baseline timing
7. Isolated tempdir diff-scoped AST micro-mutation testing with per-mutant timeout & configurable max_mutants
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".next", ".venv", "venv",
    ".idea", ".vscode", ".agents", ".githooks", "dist", "build",
}
POLYGLOT_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java"}
NON_FIELD_BASE_NAMES = {"object", "ABC", "Generic", "Protocol"}

SECRET_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z\-_]{35})\b")
]


def resolve_import_module(rel_path: str, module: Optional[str], level: int) -> str:
    """Resolve Python relative imports (level > 0) against a file's repo-relative path."""
    if level <= 0:
        return module or ""
    pkg_parts = list(Path(rel_path).parent.parts)
    up = level - 1
    if up > len(pkg_parts):
        return ""
    base = pkg_parts[: len(pkg_parts) - up] if up > 0 else pkg_parts
    if module:
        return ".".join(base + [p for p in module.split(".") if p])
    return ".".join(base)


def _strip_comments_keep_strings(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "*":
            out.append(" ")
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch in ('"', "'", "`"):
            quote = ch
            out.append(quote)
            i += 1
            while i < n:
                c = text[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_polyglot_imports(file_path: Path) -> list[str]:
    """Extract imported module specifiers from comment-stripped TS/JS/Go/Rust/Java files."""
    try:
        cleaned = _strip_comments_keep_strings(file_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    suffix = file_path.suffix.lower()
    imports: list[str] = []
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
        patterns = [
            r"""\bfrom\s+["']([^"']+)["']""",
            r"""\bimport\s+["']([^"']+)["']""",
            r"""\brequire\(\s*["']([^"']+)["']\s*\)""",
        ]
        for pat in patterns:
            imports.extend(re.findall(pat, cleaned))
    elif suffix == ".go":
        for blk in re.findall(r"""import\s*\(([^)]*)\)""", cleaned, re.S):
            imports.extend(re.findall(r'"([^"]+)"', blk))
        imports.extend(re.findall(r'^import\s+(?:[A-Za-z_][A-Za-z0-9_]*\s+)?"([^"]+)"', cleaned, re.M))
    elif suffix == ".rs":
        imports.extend(re.findall(r"^\s*(?:pub\s+)?use\s+([A-Za-z0-9_:]+)", cleaned, re.M))
    elif suffix == ".java":
        imports.extend(re.findall(r"^\s*import\s+(?:static\s+)?([A-Za-z0-9_.]+)", cleaned, re.M))
    return imports


def extract_imports(py_path: Path, workspace_root: Optional[Path] = None) -> list[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    rel_path = ""
    if workspace_root is not None:
        try:
            rel_path = py_path.relative_to(workspace_root).as_posix()
        except Exception:
            rel_path = py_path.name
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 and rel_path:
                resolved = resolve_import_module(rel_path, node.module, node.level)
                if resolved:
                    imports.append(resolved)
                    if node.module is None and workspace_root is not None:
                        base_dir = workspace_root.joinpath(*resolved.split("."))
                        for alias in node.names:
                            if alias.name != "*" and (
                                (base_dir / alias.name).is_dir() or (base_dir / f"{alias.name}.py").is_file()
                            ):
                                imports.append(f"{resolved}.{alias.name}")
            elif node.module:
                imports.append(node.module)
    return imports


def is_internal_module(imp: str, repo_root: Path) -> bool:
    if not imp:
        return False
    top = imp.split(".")[0]
    if not top or top in {"tests", "scripts", "docs"}:
        return False
    return (repo_root / top).is_dir() or (repo_root / f"{top}.py").is_file()


def _is_abstract_method(node: ast.AST) -> bool:
    for dec in getattr(node, "decorator_list", []):
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
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not _is_abstract_method(node) and not is_test_or_verifier:
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
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if (isinstance(target, ast.Name) and target.id == "dataclass") or (
            isinstance(target, ast.Attribute) and target.attr == "dataclass"
        ):
            return True
    return False


def _dataclass_has_init_false(node: ast.ClassDef) -> bool:
    for d in node.decorator_list:
        if isinstance(d, ast.Call):
            target = d.func
            if (isinstance(target, ast.Name) and target.id == "dataclass") or (
                isinstance(target, ast.Attribute) and target.attr == "dataclass"
            ):
                for kw in d.keywords:
                    if kw.arg == "init" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                        return True
    return False


def _attr_or_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _attr_or_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _attr_or_name(node.value)
    return ""


def _is_non_init_annassign(item: ast.AnnAssign) -> tuple[bool, bool]:
    """Return (exclude_from_all_fields, exclude_from_init_args)."""
    ann_name = _attr_or_name(item.annotation)
    ann_tail = ann_name.split(".")[-1] if ann_name else ""
    if ann_tail == "KW_ONLY":
        return True, True
    if ann_tail == "ClassVar":
        return False, True
    if isinstance(item.value, ast.Call):
        fn_name = _attr_or_name(item.value.func)
        if fn_name in {"field", "dataclasses.field"}:
            for kw in item.value.keywords:
                if kw.arg == "init" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                    return False, True
    return False, False


def _extract_dataclass_fields(tree: ast.Module) -> dict[str, set[str]]:
    dc_map: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_dataclass_def(node):
            fields: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    skip_all, _ = _is_non_init_annassign(item)
                    if not skip_all:
                        fields.add(item.target.id)
            dc_map[node.name] = fields
    return dc_map


def _extract_dataclass_specs(tree: ast.Module) -> dict[str, tuple[Optional[set[str]], list[str]]]:
    specs: dict[str, tuple[Optional[set[str]], list[str]]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_dataclass_def(node):
            has_custom_init = _dataclass_has_init_false(node) or any(
                isinstance(item, ast.FunctionDef) and item.name == "__init__" for item in node.body
            )
            bases = [_attr_or_name(b) for b in node.bases]
            bases = [b for b in bases if b]
            if has_custom_init:
                specs[node.name] = (None, bases)
                continue
            init_fields: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    _, skip_init = _is_non_init_annassign(item)
                    if not skip_init:
                        init_fields.add(item.target.id)
            specs[node.name] = (init_fields, bases)
    return specs


def _is_submodule_on_disk(pkg_mod: str, name: str, workspace_root: Path) -> bool:
    mod_dir = workspace_root.joinpath(*pkg_mod.split("."))
    return (mod_dir / name).is_dir() or (mod_dir / f"{name}.py").is_file()


def check_symbol_and_dataclass_resolution(workspace_root: Path) -> list[str]:
    issues: list[str] = []
    module_exports: dict[str, set[str]] = {}
    # fq_cls -> (init_fields_or_None, base_names, defining_module)
    raw_dc_specs: dict[str, tuple[Optional[set[str]], list[str], str]] = {}
    # fq_symbol -> canonical fq_cls (propagated across re-exports)
    dc_aliases: dict[str, str] = {}
    # module -> local_symbol -> (target_module, orig_symbol)
    module_from_bindings: dict[str, dict[str, tuple[str, str]]] = {}
    # module -> local_alias -> target_module
    module_mod_aliases: dict[str, dict[str, str]] = {}
    raw_from_imports: list[tuple[str, str, str, str]] = []  # (importer_mod, src_mod, orig_name, as_name)
    parsed_files: list[tuple[str, str, ast.Module]] = []

    for py_file in sorted(workspace_root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in py_file.relative_to(workspace_root).parts):
            continue
        rel = py_file.relative_to(workspace_root).as_posix()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue

        if py_file.name == "__init__.py":
            mod_name = ".".join(py_file.relative_to(workspace_root).parent.parts)
        else:
            mod_name = ".".join(py_file.relative_to(workspace_root).with_suffix("").parts)
        if not mod_name:
            mod_name = py_file.stem

        parsed_files.append((rel, mod_name, tree))
        exports: set[str] = set()
        bindings: dict[str, tuple[str, str]] = {}
        aliases: dict[str, str] = {}

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
                    bound = alias.asname or alias.name.split(".")[0]
                    exports.add(bound)
                    if is_internal_module(alias.name, workspace_root):
                        if alias.asname or "." not in alias.name:
                            aliases[bound] = alias.name
            elif isinstance(node, ast.ImportFrom):
                src_mod = (
                    resolve_import_module(rel, node.module, node.level)
                    if node.level > 0
                    else (node.module or "")
                )
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    raw_from_imports.append((mod_name, src_mod, alias.name, local_name))
                    if alias.name != "*":
                        bindings[local_name] = (src_mod, alias.name)
                        if src_mod and is_internal_module(src_mod, workspace_root):
                            if _is_submodule_on_disk(src_mod, alias.name, workspace_root):
                                aliases[local_name] = f"{src_mod}.{alias.name}"
                        if not src_mod or not is_internal_module(src_mod, workspace_root):
                            exports.add(local_name)

        module_exports[mod_name] = exports
        module_from_bindings[mod_name] = bindings
        module_mod_aliases[mod_name] = aliases

        for cls_name, (fields, bases) in _extract_dataclass_specs(tree).items():
            fq = f"{mod_name}.{cls_name}"
            raw_dc_specs[fq] = (fields, bases, mod_name)
            dc_aliases[fq] = fq

    # Also capture local module aliases inside function bodies for verification
    # Fixed-point propagation of re-exports across __init__.py and modules
    for _ in range(8):
        changed = False
        for importer_mod, src_mod, orig_name, local_name in raw_from_imports:
            if not src_mod or src_mod not in module_exports:
                continue
            exp_set = module_exports[importer_mod]
            if orig_name == "*":
                for sym in module_exports[src_mod]:
                    if not sym.startswith("_") and sym not in exp_set:
                        exp_set.add(sym)
                        changed = True
                    src_fq = f"{src_mod}.{sym}"
                    dst_fq = f"{importer_mod}.{sym}"
                    if src_fq in dc_aliases and dst_fq not in dc_aliases:
                        dc_aliases[dst_fq] = dc_aliases[src_fq]
                        changed = True
            else:
                if orig_name in module_exports[src_mod] or _is_submodule_on_disk(src_mod, orig_name, workspace_root):
                    if local_name not in exp_set:
                        exp_set.add(local_name)
                        changed = True
                src_fq = f"{src_mod}.{orig_name}"
                dst_fq = f"{importer_mod}.{local_name}"
                if src_fq in dc_aliases and dst_fq not in dc_aliases:
                    dc_aliases[dst_fq] = dc_aliases[src_fq]
                    changed = True
        if not changed:
            break

    # Resolve inherited dataclass init fields
    resolved_dc_fields: dict[str, Optional[set[str]]] = {}

    def _resolve_dc_fields(fq_cls: str, stack: set[str]) -> Optional[set[str]]:
        if fq_cls in resolved_dc_fields:
            return resolved_dc_fields[fq_cls]
        canon = dc_aliases.get(fq_cls, fq_cls)
        if canon not in raw_dc_specs or canon in stack:
            return None
        own_fields, bases, def_mod = raw_dc_specs[canon]
        if own_fields is None:
            resolved_dc_fields[canon] = None
            return None

        combined: Optional[set[str]] = set(own_fields)
        new_stack = stack | {canon}
        bindings = module_from_bindings.get(def_mod, {})
        mod_aliases = module_mod_aliases.get(def_mod, {})

        for base_expr in bases:
            base_short = base_expr.split(".")[-1]
            if base_short in NON_FIELD_BASE_NAMES or base_expr in NON_FIELD_BASE_NAMES:
                continue
            target_fq: Optional[str] = None
            if "." in base_expr:
                head, tail = base_expr.split(".", 1)
                if head in mod_aliases and "." not in tail:
                    target_fq = f"{mod_aliases[head]}.{tail}"
            elif base_expr in bindings:
                b_mod, b_orig = bindings[base_expr]
                if not b_mod or not is_internal_module(b_mod, workspace_root):
                    # External base class (e.g. BaseModel) -> do not false-positive
                    combined = None
                    break
                target_fq = f"{b_mod}.{b_orig}"
            elif f"{def_mod}.{base_expr}" in dc_aliases:
                target_fq = f"{def_mod}.{base_expr}"
            elif f"{def_mod}.{base_expr}" in raw_dc_specs:
                target_fq = f"{def_mod}.{base_expr}"

            if target_fq is None or target_fq not in dc_aliases:
                # Inherits from non-dataclass or unindexed external/complex base
                combined = None
                break
            parent_fields = _resolve_dc_fields(dc_aliases[target_fq], new_stack)
            if parent_fields is None:
                combined = None
                break
            assert combined is not None
            combined |= parent_fields

        resolved_dc_fields[canon] = combined
        return combined

    for fq_cls in list(raw_dc_specs):
        _resolve_dc_fields(fq_cls, set())
    for alias_fq, canon_fq in dc_aliases.items():
        if canon_fq in resolved_dc_fields:
            resolved_dc_fields[alias_fq] = resolved_dc_fields[canon_fq]

    # Verify each file's imports, module alias attribute references, and dataclass calls
    for rel, mod_name, tree in parsed_files:
        imported_dataclasses: dict[str, Optional[set[str]]] = {}
        local_aliases: dict[str, str] = dict(module_mod_aliases.get(mod_name, {}))

        for fq_cls, fields in resolved_dc_fields.items():
            if fq_cls.startswith(mod_name + ".") and fq_cls.count(".") == mod_name.count(".") + 1:
                cls_short = fq_cls.rsplit(".", 1)[-1]
                imported_dataclasses[cls_short] = fields

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if is_internal_module(alias.name, workspace_root):
                        if alias.asname:
                            local_aliases[alias.asname] = alias.name
                        elif "." not in alias.name:
                            local_aliases[alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                src_mod = (
                    resolve_import_module(rel, node.module, node.level)
                    if node.level > 0
                    else (node.module or "")
                )
                if src_mod and is_internal_module(src_mod, workspace_root) and src_mod in module_exports:
                    for alias in node.names:
                        if alias.name == "*":
                            for sym in module_exports[src_mod]:
                                fq_sym = f"{src_mod}.{sym}"
                                if fq_sym in resolved_dc_fields:
                                    imported_dataclasses[sym] = resolved_dc_fields[fq_sym]
                            continue
                        is_submod = _is_submodule_on_disk(src_mod, alias.name, workspace_root)
                        if is_submod:
                            local_aliases[alias.asname or alias.name] = f"{src_mod}.{alias.name}"
                        elif alias.name not in module_exports[src_mod]:
                            issues.append(
                                f"[Hallucinated Import] {rel}:{node.lineno} imports `{alias.name}` from `{src_mod}`, which is not defined there."
                            )
                        fq = f"{src_mod}.{alias.name}"
                        if fq in resolved_dc_fields:
                            imported_dataclasses[alias.asname or alias.name] = resolved_dc_fields[fq]

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                alias_name = node.value.id
                if alias_name in local_aliases and not node.attr.startswith("__"):
                    target_mod = local_aliases[alias_name]
                    if target_mod in module_exports:
                        if (
                            node.attr not in module_exports[target_mod]
                            and not _is_submodule_on_disk(target_mod, node.attr, workspace_root)
                        ):
                            issues.append(
                                f"[Hallucinated Symbol] {rel}:{node.lineno} references `{alias_name}.{node.attr}`, "
                                f"which is not defined in `{target_mod}`."
                            )
            elif isinstance(node, ast.Call):
                valid_fields: Optional[set[str]] = None
                cls_display = ""
                if isinstance(node.func, ast.Name) and node.func.id in imported_dataclasses:
                    cls_display = node.func.id
                    valid_fields = imported_dataclasses[cls_display]
                elif (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in local_aliases
                ):
                    target_mod = local_aliases[node.func.value.id]
                    fq_call = f"{target_mod}.{node.func.attr}"
                    if fq_call in resolved_dc_fields:
                        cls_display = f"{node.func.value.id}.{node.func.attr}"
                        valid_fields = resolved_dc_fields[fq_call]

                if cls_display and valid_fields is not None:
                    for kw in node.keywords:
                        if kw.arg is not None and kw.arg not in valid_fields:
                            issues.append(
                                f"[Hallucinated Dataclass Field] {rel}:{node.lineno} passes unknown/non-init keyword `{kw.arg}` to dataclass `{cls_display}`."
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


def _check_config_and_hooks_tampering(workspace_root: Path, new_cfg: dict) -> list[str]:
    issues: list[str] = []
    head_cfg_proc = subprocess.run(
        ["git", "show", "HEAD:.ai-native.json"],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
    )
    if head_cfg_proc.returncode == 0:
        if not (workspace_root / ".ai-native.json").exists():
            issues.append("[Guardrail Tampering Blocked] Tracked `.ai-native.json` was deleted.")
        else:
            try:
                old_cfg = json.loads(head_cfg_proc.stdout)
                if old_cfg.get("mutation_testing", False) and not new_cfg.get("mutation_testing", False):
                    issues.append(
                        "[Guardrail Tampering Blocked] `.ai-native.json` weakened `mutation_testing` from true to false."
                    )
                if old_cfg.get("enforce_contract_compatibility", True) and not new_cfg.get("enforce_contract_compatibility", True):
                    issues.append(
                        "[Guardrail Tampering Blocked] `.ai-native.json` weakened `enforce_contract_compatibility` from true to false."
                    )
                old_budget = old_cfg.get("context_budget", {})
                new_budget = new_cfg.get("context_budget", {})
                if int(new_budget.get("max_lines", 200)) > int(old_budget.get("max_lines", 200)):
                    issues.append("[Guardrail Tampering Blocked] `.ai-native.json` increased `context_budget.max_lines`.")
                if int(new_budget.get("max_bytes", 20000)) > int(old_budget.get("max_bytes", 20000)):
                    issues.append("[Guardrail Tampering Blocked] `.ai-native.json` increased `context_budget.max_bytes`.")
                if "max_layers_touched" in old_cfg:
                    old_mlt = old_cfg["max_layers_touched"]
                    new_mlt = new_cfg.get("max_layers_touched")
                    if old_mlt is not None and (new_mlt is None or int(new_mlt) > int(old_mlt)):
                        issues.append("[Guardrail Tampering Blocked] `.ai-native.json` weakened `max_layers_touched`.")
                new_layers = {layer.get("path"): layer for layer in new_cfg.get("layers", []) if layer.get("path")}
                for old_layer in old_cfg.get("layers", []):
                    lp = old_layer.get("path")
                    if not lp:
                        continue
                    if lp not in new_layers:
                        issues.append(f"[Guardrail Tampering Blocked] `.ai-native.json` removed layer `{lp}`.")
                        continue
                    old_forb = set(old_layer.get("forbidden_imports", []))
                    new_forb = set(new_layers[lp].get("forbidden_imports", []))
                    removed_forb = sorted(old_forb - new_forb)
                    if removed_forb:
                        issues.append(
                            f"[Guardrail Tampering Blocked] `.ai-native.json` removed forbidden_imports {removed_forb} from `{lp}`."
                        )
            except Exception:
                pass

    for hook_rel in (".githooks/pre-commit", ".githooks/pre-push"):
        tracked = subprocess.run(
            ["git", "show", f"HEAD:{hook_rel}"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
        )
        if tracked.returncode == 0:
            hook_path = workspace_root / hook_rel
            if not hook_path.exists():
                issues.append(f"[Guardrail Tampering Blocked] Tracked `{hook_rel}` was deleted.")
            elif sys.platform != "win32" and not os.access(hook_path, os.X_OK):
                issues.append(f"[Guardrail Tampering Blocked] `{hook_rel}` lost executable permission.")

    return issues


def check_git_head_regressions(workspace_root: Path, config: dict) -> list[str]:
    if not _has_git_head(workspace_root):
        return []
    issues = _check_config_and_hooks_tampering(workspace_root, config)

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
                if Path(p).suffix.lower() in ({".py"} | POLYGLOT_EXTENSIONS):
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


def run_diff_mutation_check(
    workspace_root: Path,
    test_cmd: str,
    max_mutants: int = 25,
    baseline_duration: float = 0.0,
    timeout_sec: Optional[float] = None,
) -> list[str]:
    """Run diff-scoped AST micro-mutations inside an isolated tempdir copy with hard per-mutant timeout."""
    if not test_cmd or max_mutants <= 0 or not _has_git_head(workspace_root):
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
    candidates: list[tuple[str, int, int, int, str, str]] = []

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

            if isinstance(node, ast.Compare):
                mutated_code = f"({mutated_code})"

            candidates.append((rel, node.lineno, node.col_offset, node.end_col_offset, orig_seg, mutated_code))
            if len(candidates) >= max_mutants:
                break
        if len(candidates) >= max_mutants:
            break

    if not candidates:
        return []

    per_mutant_timeout = float(timeout_sec) if timeout_sec is not None else max(5.0, baseline_duration * 3.0)
    issues: list[str] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_ws = Path(tmp_dir) / "repo"
        shutil.copytree(
            workspace_root,
            tmp_ws,
            symlinks=True,
            ignore=shutil.ignore_patterns(*IGNORE_DIRS),
        )
        for rel, lineno, col_s, col_e, orig_seg, mutated_code in candidates:
            tmp_py_path = tmp_ws / rel
            if not tmp_py_path.exists():
                continue
            orig_text = tmp_py_path.read_text(encoding="utf-8")
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
                tmp_py_path.write_text(mut_text, encoding="utf-8")
                try:
                    proc = subprocess.run(
                        test_cmd,
                        shell=True,
                        cwd=str(tmp_ws),
                        capture_output=True,
                        text=True,
                        timeout=per_mutant_timeout,
                    )
                    if proc.returncode == 0:
                        issues.append(
                            f"[Weak Test / Surviving Mutant] {rel}:{lineno} — inverting `{orig_seg}` -> `{mutated_code}` did not fail any test."
                        )
                except subprocess.TimeoutExpired:
                    # Infinite-loop or hung mutant is treated as killed/timed-out cleanly
                    pass
            finally:
                tmp_py_path.write_text(orig_text, encoding="utf-8")

    return issues


def verify_workspace(workspace_root: Path, run_tests: bool = True, force_mutate: bool = False) -> list[str]:
    errors: list[str] = []
    config_path = workspace_root / ".ai-native.json"
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"[Config Error] Invalid .ai-native.json: {exc}")
            return errors

    # 1. Syntax, LLM Smells, Stubs & Secrets Check (Tier 1 Python)
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

    # 3. Layer Import Boundary Check (Python AST + Tier 2 Polyglot Lexical Imports)
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
            if any(part in IGNORE_DIRS for part in pf.relative_to(workspace_root).parts):
                continue
            rel = pf.relative_to(workspace_root).as_posix()
            for imp in extract_imports(pf, workspace_root=workspace_root):
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

        if forbidden:
            poly_targets = (
                [layer_path]
                if layer_path.is_file() and layer_path.suffix.lower() in POLYGLOT_EXTENSIONS
                else sorted(
                    p for p in layer_path.rglob("*")
                    if p.is_file()
                    and p.suffix.lower() in POLYGLOT_EXTENSIONS
                    and not any(part in IGNORE_DIRS for part in p.relative_to(workspace_root).parts)
                )
            )
            for tf in poly_targets:
                rel = tf.relative_to(workspace_root).as_posix()
                for imp in extract_polyglot_imports(tf):
                    for bad in forbidden:
                        if bad in imp or matches_prefix(imp.replace("/", "."), bad):
                            errors.append(
                                f"[Boundary Violation] {rel} imports `{imp}`, which matches forbidden `{bad}` in layer `{layer['path']}`."
                            )

    # 4. Anti-Test-Tampering, Public Contract Regression, Config/Hooks Tampering & Blast-Radius Check vs HEAD
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
    baseline_duration = 0.0
    if run_tests and test_cmd:
        t0 = time.monotonic()
        proc = subprocess.run(
            test_cmd,
            shell=True,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
        )
        baseline_duration = time.monotonic() - t0
        if proc.returncode != 0:
            out = (proc.stdout + "\n" + proc.stderr).strip()[-1000:]
            errors.append(f"[Unit Test Failure] `{test_cmd}` failed:\n{out}")

    # 7. Isolated Diff-Scoped AST Micro-Mutation Testing
    if not errors and run_tests and test_cmd and (force_mutate or config.get("mutation_testing", False)):
        max_mutants = int(config.get("max_mutants", 25))
        errors.extend(
            run_diff_mutation_check(
                workspace_root,
                test_cmd,
                max_mutants=max_mutants,
                baseline_duration=baseline_duration,
            )
        )

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
