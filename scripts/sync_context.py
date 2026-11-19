#!/usr/bin/env python3
"""
Polyglot Context & ADR Introspector (v3.2 — Pure Python 3 Stdlib)
Updates <!-- AUTO-GENERATED:START --> ... <!-- AUTO-GENERATED:END --> blocks
across all AGENTS.md files with:
- Tier 1 Python (.py): Full AST classes, dataclasses, async/sync functions, constants,
  relative/absolute imports, reverse callers (Used By), and test mappings (Tested By)
- Tier 2 Polyglot (.ts, .tsx, .js, .jsx, .mjs, .go, .rs, .java): Comment/string-stripped lexical exports
- Machine-readable ADR index (from docs/adr/*.md YAML frontmatter, CRLF & list safe)
- Single-hop Context Capsule (--for <file>) & session context ledger helpers
- Context budget telemetry (line count)
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

START_MARKER = "<!-- AUTO-GENERATED:START -->"
END_MARKER = "<!-- AUTO-GENERATED:END -->"
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".next", ".venv", "venv",
    ".idea", ".vscode", ".agents", ".githooks", "dist", "build",
}
POLYGLOT_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java"}


def format_annotation(node) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _strip_matching_quotes(val: str) -> str:
    v = val.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def resolve_relative_import(rel_path: str, module: Optional[str], level: int) -> str:
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


def is_internal_module(mod_name: str, repo_root: Path) -> bool:
    if not mod_name:
        return False
    top = mod_name.split(".")[0]
    if not top or top in {"tests", "scripts", "docs"}:
        return False
    return (repo_root / top).is_dir() or (repo_root / f"{top}.py").is_file()


def _parse_applies_to_value(raw_val: str) -> list[str]:
    v = raw_val.strip()
    if not v:
        return []
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1]
        items = [_strip_matching_quotes(part.strip()) for part in inner.split(",")]
        return [i for i in items if i]
    if "," in v and not (v.startswith('"') or v.startswith("'")):
        items = [_strip_matching_quotes(part.strip()) for part in v.split(",")]
        return [i for i in items if i]
    single = _strip_matching_quotes(v)
    return [single] if single else []


def parse_adr_frontmatter(adr_path: Path, repo_root: Path) -> dict:
    """Parse ADR YAML frontmatter safely across CRLF endings, body `---` rules, quoted colons, and lists."""
    rel_path = adr_path.relative_to(repo_root).as_posix()
    meta: dict = {
        "rel_path": rel_path,
        "id": adr_path.stem.split("-")[0].upper(),
        "title": adr_path.stem,
        "status": "accepted",
        "applies_to": "*",
        "applies_to_list": ["*"],
        "summary": "",
    }
    try:
        raw = adr_path.read_text(encoding="utf-8")
    except Exception:
        return meta

    text = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    open_m = re.match(r"^---[ \t]*\n", text)
    if not open_m:
        return meta

    rest = text[open_m.end():]
    close_m = re.search(r"(?:^|\n)---[ \t]*(?:\n|$)", rest)
    if not close_m:
        return meta

    fm = rest[:close_m.start()]
    current_list_key: Optional[str] = None
    parsed_lists: dict[str, list[str]] = {}

    for line in fm.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        list_item_m = re.match(r"^[ \t]+-[ \t]+(.+)$", line)
        if list_item_m and current_list_key:
            item_val = _strip_matching_quotes(list_item_m.group(1).strip())
            if item_val:
                parsed_lists.setdefault(current_list_key, []).append(item_val)
            continue

        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v_str = v.strip()
            current_list_key = None
            if k == "applies_to":
                if not v_str:
                    current_list_key = "applies_to"
                    parsed_lists["applies_to"] = []
                else:
                    vals = _parse_applies_to_value(v_str)
                    if vals:
                        meta["applies_to_list"] = vals
                        meta["applies_to"] = ", ".join(vals)
            elif k in meta:
                meta[k] = _strip_matching_quotes(v_str)

    if "applies_to" in parsed_lists and parsed_lists["applies_to"]:
        meta["applies_to_list"] = parsed_lists["applies_to"]
        meta["applies_to"] = ", ".join(parsed_lists["applies_to"])

    return meta


def adr_matches_directory(adr: dict, rel_dir: str) -> bool:
    patterns = adr.get("applies_to_list") or _parse_applies_to_value(str(adr.get("applies_to", "*"))) or ["*"]
    for pat in patterns:
        pat_clean = pat.strip()
        if not pat_clean:
            continue
        if pat_clean == "*":
            return True
        if fnmatch.fnmatch(rel_dir, pat_clean.rstrip("/*")):
            return True
        if fnmatch.fnmatch(f"{rel_dir}/", pat_clean):
            return True
        if pat_clean == rel_dir or pat_clean.startswith(rel_dir + "/"):
            return True
    return False


def adr_matches_path(adr: dict, rel_path: str) -> bool:
    norm = Path(rel_path).as_posix().lstrip("./")
    patterns = adr.get("applies_to_list") or _parse_applies_to_value(str(adr.get("applies_to", "*"))) or ["*"]
    for pat in patterns:
        pat_clean = pat.strip().lstrip("./")
        if not pat_clean:
            continue
        if pat_clean == "*":
            return True
        if fnmatch.fnmatch(norm, pat_clean):
            return True
        dir_prefix = pat_clean.rstrip("/*")
        if dir_prefix and (norm == dir_prefix or norm.startswith(dir_prefix + "/")):
            return True
    return False


def _attr_chain(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _attr_chain(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _extract_used_refs(node: ast.AST) -> set[str]:
    refs: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            refs.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            chain = _attr_chain(sub)
            if chain:
                refs.add(chain)
    return refs


def _is_polyglot_test_file(rel: str) -> bool:
    norm = rel.lstrip("./")
    name = Path(norm).name
    return (
        norm.startswith("tests/")
        or norm.startswith("test/")
        or "/__tests__/" in f"/{norm}"
        or name.endswith((
            ".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx",
            ".test.js", ".spec.js", ".test.jsx", ".spec.jsx",
            "_test.go", "_test.rs", "Test.java", "Tests.java",
        ))
    )


def _extract_polyglot_import_specs(raw_text: str, suffix: str) -> list[str]:
    """Extract import/require specifiers from comment-stripped non-Python code while preserving string quotes."""
    # Remove /* ... */ and // comments without erasing quoted module strings
    out: list[str] = []
    i, n = 0, len(raw_text)
    while i < n:
        ch = raw_text[i]
        nxt = raw_text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "*":
            out.append(" ")
            i += 2
            while i < n and not (raw_text[i] == "*" and i + 1 < n and raw_text[i + 1] == "/"):
                if raw_text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < n and raw_text[i] != "\n":
                i += 1
            continue
        if ch in ('"', "'", "`"):
            quote = ch
            out.append(quote)
            i += 1
            while i < n:
                c = raw_text[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(raw_text[i + 1])
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    specs: list[str] = []
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
        for pat in (r"""\bfrom\s+["']([^"']+)["']""", r"""\bimport\s+["']([^"']+)["']""", r"""\brequire\(\s*["']([^"']+)["']\s*\)"""):
            specs.extend(re.findall(pat, cleaned))
    elif suffix == ".go":
        for blk in re.findall(r"""import\s*\(([^)]*)\)""", cleaned, re.S):
            specs.extend(re.findall(r'"([^"]+)"', blk))
        specs.extend(re.findall(r'^import\s+(?:[A-Za-z_][A-Za-z0-9_]*\s+)?"([^"]+)"', cleaned, re.M))
    elif suffix == ".rs":
        specs.extend(re.findall(r"^\s*(?:pub\s+)?use\s+([A-Za-z0-9_:]+)", cleaned, re.M))
    elif suffix == ".java":
        specs.extend(re.findall(r"^\s*import\s+(?:static\s+)?([A-Za-z0-9_.]+)", cleaned, re.M))
    return specs


def build_reverse_index(repo_root: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Build repo-wide reverse maps for Python and Tier-2 polyglot modules:
    - used_by[target_rel]: sorted non-test source files importing/referencing target_rel
    - tested_by[target_rel]: sorted `test_file::test_fn` (or `test_file`) referencing target_rel
    """
    mod_to_rel: dict[str, str] = {}
    py_files: list[tuple[str, Path]] = []

    for pf in sorted(repo_root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in pf.relative_to(repo_root).parts):
            continue
        rel = pf.relative_to(repo_root).as_posix()
        py_files.append((rel, pf))
        if pf.name == "__init__.py":
            mod_name = ".".join(pf.relative_to(repo_root).parent.parts)
        else:
            mod_name = ".".join(pf.relative_to(repo_root).with_suffix("").parts)
        if mod_name:
            mod_to_rel[mod_name] = rel

    used_by_sets: dict[str, set[str]] = {}
    tested_by_sets: dict[str, set[str]] = {}

    for rel, pf in py_files:
        try:
            tree = ast.parse(pf.read_text(encoding="utf-8"), filename=str(pf))
        except Exception:
            continue

        target_refs: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    t_rel = mod_to_rel.get(alias.name)
                    if t_rel and t_rel != rel:
                        ref = alias.asname or alias.name
                        target_refs.setdefault(t_rel, set()).add(ref)
            elif isinstance(node, ast.ImportFrom):
                base_mod = (
                    resolve_relative_import(rel, node.module, node.level)
                    if node.level > 0
                    else (node.module or "")
                )
                if not base_mod:
                    continue
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    sub_mod = f"{base_mod}.{alias.name}" if alias.name != "*" else ""
                    if sub_mod and sub_mod in mod_to_rel:
                        t_rel = mod_to_rel[sub_mod]
                        if t_rel != rel:
                            target_refs.setdefault(t_rel, set()).add(local_name)
                    elif base_mod in mod_to_rel:
                        t_rel = mod_to_rel[base_mod]
                        if t_rel != rel:
                            target_refs.setdefault(t_rel, set()).add("*" if alias.name == "*" else local_name)

        if not target_refs:
            continue

        is_test = rel.startswith("tests/") or pf.name.startswith("test_")
        if not is_test:
            for t_rel in target_refs:
                used_by_sets.setdefault(t_rel, set()).add(rel)
        else:
            test_fns = [
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
            ]
            matched_targets: set[str] = set()
            for fn in test_fns:
                used = _extract_used_refs(fn)
                for t_rel, refs in target_refs.items():
                    if "*" in refs or (refs & used):
                        tested_by_sets.setdefault(t_rel, set()).add(f"{rel}::{fn.name}")
                        matched_targets.add(t_rel)
            for t_rel in target_refs:
                if t_rel not in matched_targets:
                    tested_by_sets.setdefault(t_rel, set()).add(rel)

    # Polyglot reverse indexing (TS/JS/Go/Rust/Java)
    poly_files = _iter_polyglot_files(repo_root)
    if poly_files:
        poly_meta: dict[str, tuple[str, str, set[str]]] = {}
        for tf in poly_files:
            t_info = inspect_polyglot_file(tf, repo_root)
            t_rel = t_info["rel_path"]
            t_no_ext = posixpath.splitext(t_rel)[0]
            t_stem = Path(t_no_ext).name
            poly_meta[t_rel] = (t_no_ext, t_stem, set(t_info.get("exports", [])))

        for tf in poly_files:
            rel = tf.relative_to(repo_root).as_posix()
            try:
                raw = tf.read_text(encoding="utf-8")
            except Exception:
                continue
            specs = _extract_polyglot_import_specs(raw, tf.suffix.lower())
            code_idents = set(re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", strip_comments_and_strings(raw)))
            src_dir = posixpath.dirname(rel)
            resolved_specs: set[str] = set()
            for s in specs:
                if s.startswith("."):
                    norm_s = posixpath.normpath(posixpath.join(src_dir, s) if src_dir else s)
                    resolved_specs.add(posixpath.splitext(norm_s)[0])
                    if norm_s.endswith("/index"):
                        resolved_specs.add(norm_s[:-6])

            is_test = _is_polyglot_test_file(rel)
            for t_rel, (t_no_ext, t_stem, exp_syms) in poly_meta.items():
                if t_rel == rel:
                    continue
                t_dir_if_index = t_no_ext[:-6] if t_no_ext.endswith("/index") else ""
                matched = (t_no_ext in resolved_specs) or (bool(t_dir_if_index) and t_dir_if_index in resolved_specs)
                if not matched:
                    for s in specs:
                        if s.endswith(t_no_ext) or (
                            exp_syms
                            and (exp_syms & code_idents)
                            and (
                                s == t_stem
                                or s.endswith("/" + t_stem)
                                or s.endswith("." + t_stem)
                                or s.endswith("::" + t_stem)
                            )
                        ):
                            matched = True
                            break
                if matched:
                    if is_test:
                        tested_by_sets.setdefault(t_rel, set()).add(rel)
                    else:
                        used_by_sets.setdefault(t_rel, set()).add(rel)

    used_by = {k: sorted(v) for k, v in used_by_sets.items()}
    tested_by = {k: sorted(v) for k, v in tested_by_sets.items()}
    return used_by, tested_by


def _normalize_rel_path(repo_root: Path, file_path: str) -> str:
    p = Path(file_path)
    resolved_root = repo_root.resolve()
    if p.is_absolute():
        try:
            return p.resolve().relative_to(resolved_root).as_posix()
        except Exception:
            return p.as_posix()
    return p.as_posix().lstrip("./")


def get_context_ledger_path(repo_root: Path) -> Path:
    resolved = repo_root.resolve()
    git_dir = resolved / ".git"
    if git_dir.is_dir() and os.access(git_dir, os.W_OK):
        return git_dir / "ai_native_context_ledger.json"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"ai_native_ledger_{digest}.json"


def _load_ledger(ledger_path: Path, now: float, ttl_seconds: int) -> dict[str, float]:
    if not ledger_path.exists():
        return {}
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {
            str(k): float(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, (int, float)) and (now - float(v)) <= ttl_seconds
        }
    except Exception:
        return {}


def record_context_read(repo_root: Path, rel_paths: list[str], ttl_seconds: int = 14400) -> None:
    if not rel_paths:
        return
    now = time.time()
    ledger_path = get_context_ledger_path(repo_root)
    entries = _load_ledger(ledger_path, now, ttl_seconds)
    for rp in rel_paths:
        norm = _normalize_rel_path(repo_root, rp)
        if norm:
            entries[norm] = now
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        digest = hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:16]
        fallback = Path(tempfile.gettempdir()) / f"ai_native_ledger_{digest}.json"
        try:
            fallback.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            pass


def _find_nearest_agents_rel(repo_root: Path, target_rel: str) -> Optional[str]:
    norm = _normalize_rel_path(repo_root, target_rel)
    cur = (repo_root / norm).parent.resolve()
    root_res = repo_root.resolve()
    while True:
        candidate = cur / "AGENTS.md"
        if candidate.is_file():
            return candidate.relative_to(root_res).as_posix()
        if cur == root_res or root_res not in cur.parents:
            break
        cur = cur.parent
    return None


def _get_required_context_files(repo_root: Path, target_rel: str) -> list[str]:
    req: list[str] = []
    agents_rel = _find_nearest_agents_rel(repo_root, target_rel)
    if agents_rel:
        req.append(agents_rel)
    for adr in load_all_adrs(repo_root):
        if adr_matches_path(adr, target_rel):
            req.append(adr["rel_path"])
    return req


def has_context_for_target(repo_root: Path, target_rel: str, ttl_seconds: int = 14400) -> tuple[bool, list[str]]:
    norm = _normalize_rel_path(repo_root, target_rel)
    required = _get_required_context_files(repo_root, norm)
    if not required:
        return True, []

    now = time.time()
    ledger_path = get_context_ledger_path(repo_root)
    entries = _load_ledger(ledger_path, now, ttl_seconds)
    if not entries:
        digest = hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:16]
        alt = Path(tempfile.gettempdir()) / f"ai_native_ledger_{digest}.json"
        if alt != ledger_path:
            entries = _load_ledger(alt, now, ttl_seconds)

    if norm in entries:
        return True, []

    missing = [f for f in required if f not in entries]
    if not missing:
        return True, []
    return False, missing


def _extract_semantic_section(agents_md: Path) -> str:
    text = agents_md.read_text(encoding="utf-8")
    start_m = re.search(rf"^[ \t]*{re.escape(START_MARKER)}[ \t]*$", text, re.M)
    end_m = re.search(rf"^[ \t]*{re.escape(END_MARKER)}[ \t]*$", text, re.M)
    if start_m and end_m and end_m.start() > start_m.end():
        return (text[:start_m.start()] + "\n" + text[end_m.end():]).strip()
    return text.strip()


def _strip_frontmatter(md_text: str) -> str:
    normalized = md_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    open_m = re.match(r"^---[ \t]*\n", normalized)
    if not open_m:
        return normalized.strip()
    rest = normalized[open_m.end():]
    close_m = re.search(r"(?:^|\n)---[ \t]*(?:\n|$)", rest)
    if not close_m:
        return normalized.strip()
    return rest[close_m.end():].strip()


def build_context_capsule(repo_root: Path, target_file: str, mark_ledger: bool = True) -> str:
    """
    Assemble a single-hop Context Capsule for `target_file`:
    - Governing scoped AGENTS.md prose
    - Full bodies of matching ADRs (Why / Invariants / Do Not Do)
    - Target file AST/lexical contract, Used By callers, and Tested By targets
    Optionally marks the session context ledger so the next edit is unlocked.
    """
    repo_root = repo_root.resolve()
    target_rel = _normalize_rel_path(repo_root, target_file)
    target_path = repo_root / target_rel

    lines: list[str] = [f"# Context Capsule for `{target_rel}`", ""]

    agents_rel = _find_nearest_agents_rel(repo_root, target_rel)
    if agents_rel and (repo_root / agents_rel).is_file():
        lines.append(f"## 1. Governing Layer Guidance (`{agents_rel}`)")
        lines.append(_extract_semantic_section(repo_root / agents_rel))
        lines.append("")

    adrs = [a for a in load_all_adrs(repo_root) if adr_matches_path(a, target_rel)]
    if adrs:
        lines.append("## 2. Architectural Rationale & Invariants (ADRs — Why / Do Not Do)")
        for adr in adrs:
            adr_file = repo_root / adr["rel_path"]
            lines.append(f"### `{adr['id']}` (`{adr['rel_path']}`): {adr['title']}")
            if adr_file.is_file():
                lines.append(_strip_frontmatter(adr_file.read_text(encoding="utf-8")))
            lines.append("")

    lines.append(f"## 3. Target Module Contract, Blast Radius & Tests (`{target_rel}`)")
    if target_path.is_file() and target_path.suffix == ".py":
        used_by, tested_by = build_reverse_index(repo_root)
        info = inspect_python_file(target_path, repo_root)
        lines.append("- **Internal Imports:** " + (", ".join(f"`{i}`" for i in info.get("internal_imports", [])) or "_None_"))
        lines.append("- **Used By (Blast Radius):** " + (", ".join(f"`{u}`" for u in used_by.get(target_rel, [])) or "_None_"))
        lines.append("- **Tested By (Verify With):** " + (", ".join(f"`{t}`" for t in tested_by.get(target_rel, [])) or "_None_"))
        if info.get("constants"):
            lines.append("- **Constants:** " + ", ".join(f"`{c}`" for c in info["constants"]))
        for cls in info.get("classes", []):
            kind = "Dataclass" if cls["is_dataclass"] else "Class"
            base_str = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
            lines.append(f"- **{kind} `{cls['name']}{base_str}`**")
            if cls["fields"]:
                lines.append("  - Fields: " + ", ".join(f"`{f}`" for f in cls["fields"]))
            for m in cls["methods"]:
                lines.append(f"  - Method: `{m}`")
        for fn in info.get("functions", []):
            lines.append(f"- **Function:** `{fn}`")
    elif target_path.is_file() and target_path.suffix.lower() in POLYGLOT_EXTENSIONS:
        used_by, tested_by = build_reverse_index(repo_root)
        t_info = inspect_polyglot_file(target_path, repo_root)
        lines.append("- **Used By (Blast Radius):** " + (", ".join(f"`{u}`" for u in used_by.get(target_rel, [])) or "_None_"))
        lines.append("- **Tested By (Verify With):** " + (", ".join(f"`{t}`" for t in tested_by.get(target_rel, [])) or "_None_"))
        lines.append("- **Exports:** " + (", ".join(f"`{e}`" for e in t_info.get("exports", [])) or "_None_"))
    elif not target_path.exists():
        lines.append(f"- _Target file `{target_rel}` does not exist on disk yet._")

    if mark_ledger:
        required = _get_required_context_files(repo_root, target_rel)
        record_context_read(repo_root, [target_rel] + required)

    return "\n".join(lines).strip() + "\n"


def load_all_adrs(repo_root: Path) -> list[dict]:
    adr_dir = repo_root / "docs" / "adr"
    if not adr_dir.exists():
        return []
    return [
        parse_adr_frontmatter(p, repo_root)
        for p in sorted(adr_dir.glob("*.md"))
        if p.name.lower() != "readme.md"
    ]


def _format_fn_sig(node: ast.AST, skip_self: bool = False) -> str:
    params = []
    for a in node.args.args:  # type: ignore[attr-defined]
        if skip_self and a.arg == "self":
            continue
        ann = format_annotation(a.annotation)
        params.append(f"{a.arg}: {ann}" if ann else a.arg)
    ret = format_annotation(node.returns)  # type: ignore[attr-defined]
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({', '.join(params)})" + (f" -> {ret}" if ret else "")  # type: ignore[attr-defined]


def inspect_python_file(py_path: Path, rel_to: Path) -> dict:
    rel_path = py_path.relative_to(rel_to).as_posix()
    info: dict = {
        "rel_path": rel_path,
        "constants": [],
        "classes": [],
        "functions": [],
        "internal_imports": [],
    }
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))
    except Exception as exc:
        info["error"] = str(exc)
        return info

    seen_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_internal_module(alias.name, rel_to) and alias.name not in seen_imports:
                    seen_imports.add(alias.name)
                    info["internal_imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = (
                resolve_relative_import(rel_path, node.module, node.level)
                if node.level > 0
                else (node.module or "")
            )
            if resolved and is_internal_module(resolved, rel_to) and resolved not in seen_imports:
                seen_imports.add(resolved)
                info["internal_imports"].append(resolved)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    info["constants"].append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                ann = format_annotation(node.annotation)
                info["constants"].append(f"{node.target.id}: {ann}" if ann else node.target.id)
        elif isinstance(node, ast.ClassDef):
            bases = [format_annotation(b) for b in node.bases]
            is_dataclass = any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                for d in node.decorator_list
            )
            fields = []
            methods = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    f_ann = format_annotation(item.annotation)
                    fields.append(f"{item.target.id}: {f_ann}" if f_ann else item.target.id)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    methods.append(_format_fn_sig(item, skip_self=True))
            info["classes"].append({
                "name": node.name,
                "bases": bases,
                "is_dataclass": is_dataclass,
                "fields": fields,
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            info["functions"].append(_format_fn_sig(node, skip_self=False))

    return info


def strip_comments_and_strings(text: str, hash_comments: bool = False) -> str:
    """
    Strip /* ... */ block comments, // (and optional #) line comments, and replace
    string/template literal contents with "" while preserving newlines.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        # Block comment /* ... */
        if ch == "/" and nxt == "*":
            out.append(" ")
            i += 2
            while i < n and not (text[i] == "*" and (i + 1 < n and text[i + 1] == "/")):
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        # Line comment //
        if ch == "/" and nxt == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        # Optional hash comment
        if hash_comments and ch == "#":
            i += 1
            while i < n and text[i] != "\n":
                i += 1
            continue
        # String or template literal: "...", '...', `...`
        if ch in ('"', "'", "`"):
            quote = ch
            out.append(quote)
            i += 1
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                if c == "\n":
                    out.append("\n")
                i += 1
            out.append(quote)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def inspect_polyglot_file(file_path: Path, rel_to: Path) -> dict:
    """Extract lexical exports from TS/JS/Go/Rust/Java after stripping comments and strings."""
    rel_path = file_path.relative_to(rel_to).as_posix()
    suffix = file_path.suffix.lower()
    exports: list[str] = []
    seen: set[str] = set()

    def _add(name: str):
        if name and name not in seen:
            seen.add(name)
            exports.append(name)

    try:
        raw = file_path.read_text(encoding="utf-8")
    except Exception:
        return {"rel_path": rel_path, "exports": []}

    cleaned = strip_comments_and_strings(raw)

    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
        decl_pat = re.compile(
            r"(?:^|[;\s}])export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
            r"(?:function\s*\*?|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            re.M,
        )
        for m in decl_pat.finditer(cleaned):
            _add(m.group(1))
        brace_pat = re.compile(r"(?:^|[;\s}])export\s+(?:type\s+)?\{([^}]+)\}", re.M)
        for m in brace_pat.finditer(cleaned):
            for item in m.group(1).split(","):
                part = item.strip()
                if not part:
                    continue
                if " as " in part:
                    _add(part.split(" as ", 1)[1].strip())
                else:
                    _add(part.split()[-1])
    elif suffix == ".go":
        go_fn = re.compile(r"^func\s+(?:\([^)]+\)\s*)?([A-Z][A-Za-z0-9_]*)\s*(?:\[|\()", re.M)
        go_type = re.compile(r"^type\s+([A-Z][A-Za-z0-9_]*)\b", re.M)
        for m in go_fn.finditer(cleaned):
            _add(m.group(1))
        for m in go_type.finditer(cleaned):
            _add(m.group(1))
    elif suffix == ".rs":
        rs_pat = re.compile(
            r"(?:^|\n)\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(?:unsafe\s+)?"
            r"(?:fn|struct|enum|trait|type|const|static|mod)\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.M,
        )
        for m in rs_pat.finditer(cleaned):
            _add(m.group(1))
    elif suffix == ".java":
        java_pat = re.compile(
            r"\bpublic\s+(?:abstract\s+|final\s+|static\s+|sealed\s+)*"
            r"(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.M,
        )
        for m in java_pat.finditer(cleaned):
            _add(m.group(1))

    return {"rel_path": rel_path, "exports": exports}


def inspect_ts_file(ts_path: Path, rel_to: Path) -> dict:
    """Backward-compatible wrapper around inspect_polyglot_file for TS/JS modules."""
    return inspect_polyglot_file(ts_path, rel_to)


def _iter_polyglot_files(dir_path: Path) -> list[Path]:
    return sorted(
        p for p in dir_path.rglob("*")
        if p.is_file()
        and p.suffix.lower() in POLYGLOT_EXTENSIONS
        and not p.name.endswith(".d.ts")
        and not any(part in IGNORE_DIRS for part in p.relative_to(dir_path).parts)
    )


def build_root_block(repo_root: Path) -> str:
    lines = [
        START_MARKER,
        "### Auto-Generated Repository & Context Map",
        "_Updated automatically from source code & ADRs by `sync_context.py`. Do not edit inside this block._",
        "",
        "#### Scoped `AGENTS.md` Hierarchy",
    ]
    agents_files = sorted(
        p for p in repo_root.rglob("AGENTS.md")
        if not any(part in IGNORE_DIRS for part in p.relative_to(repo_root).parts)
    )
    for af in agents_files:
        rel = af.relative_to(repo_root).as_posix()
        line_cnt = len(af.read_text(encoding="utf-8").splitlines())
        scope = "Repository Root" if rel == "AGENTS.md" else f"`{af.parent.relative_to(repo_root).as_posix()}/`"
        lines.append(f"- `{rel}` — Scope: {scope} ({line_cnt} lines)")

    adrs = load_all_adrs(repo_root)
    if adrs:
        lines.extend(["", "#### Active Architecture Decision Records (`docs/adr/`)"])
        for adr in adrs:
            lines.append(
                f"- **`{adr['id']}`** (`{adr['rel_path']}`) — **{adr['title']}** "
                f"[Applies to: `{adr['applies_to']}`] — {adr['summary']}"
            )

    lines.extend(["", "#### Codebase Module & Dependency Summary"])
    py_files = sorted(
        p for p in repo_root.rglob("*.py")
        if not any(part in IGNORE_DIRS for part in p.relative_to(repo_root).parts)
        and p.name != "__init__.py"
    )
    for pf in py_files:
        rel = pf.relative_to(repo_root).as_posix()
        info = inspect_python_file(pf, repo_root)
        summary_parts = []
        if info.get("classes"):
            summary_parts.append("Classes: " + ", ".join(f"`{c['name']}`" for c in info["classes"]))
        if info.get("functions"):
            fn_names = [f.split("(")[0] for f in info["functions"]]
            summary_parts.append("Functions: " + ", ".join(f"`{n}()`" for n in fn_names))
        if info.get("constants"):
            c_names = [c.split(":")[0] for c in info["constants"]]
            summary_parts.append("Constants: " + ", ".join(f"`{n}`" for n in c_names))
        if info.get("internal_imports"):
            summary_parts.append("Internal Deps: " + ", ".join(f"`{imp}`" for imp in info["internal_imports"]))
        desc = " | ".join(summary_parts) if summary_parts else "Module"
        lines.append(f"- `{rel}` — {desc}")

    for tf in _iter_polyglot_files(repo_root):
        t_info = inspect_polyglot_file(tf, repo_root)
        exp = ", ".join(f"`{e}`" for e in t_info["exports"]) if t_info["exports"] else "Module"
        lines.append(f"- `{t_info['rel_path']}` — Exports: {exp}")

    lines.append(END_MARKER)
    return "\n".join(lines)


def _format_capped_refs(items: list[str], cap: int = 8) -> str:
    shown = [f"`{it}`" for it in items[:cap]]
    if len(items) > cap:
        shown.append(f"_(+{len(items) - cap} more; use `--for`)_")
    return ", ".join(shown)


def build_directory_block(
    dir_path: Path,
    repo_root: Path,
    reverse_index: Optional[tuple[dict[str, list[str]], dict[str, list[str]]]] = None,
) -> str:
    rel_dir = dir_path.relative_to(repo_root).as_posix()
    lines = [
        START_MARKER,
        f"### Auto-Generated Live Code Contract (`{rel_dir}/`)",
        "_Updated automatically from AST & ADRs by `sync_context.py`. Do not edit inside this block._",
        "",
    ]

    adrs = load_all_adrs(repo_root)
    matching_adrs = [a for a in adrs if adr_matches_directory(a, rel_dir)]
    if matching_adrs:
        lines.append("#### Governing ADRs for This Layer")
        for adr in matching_adrs:
            lines.append(f"- **`{adr['id']}`** (`{adr['rel_path']}`): {adr['title']} — _{adr['summary']}_")
        lines.append("")

    py_files = sorted(
        p for p in dir_path.rglob("*.py")
        if not any(part in IGNORE_DIRS for part in p.relative_to(dir_path).parts)
        and p.name != "__init__.py"
    )
    poly_files = _iter_polyglot_files(dir_path)

    if not py_files and not poly_files:
        lines.append("_No source modules found in this directory._")
        lines.append(END_MARKER)
        return "\n".join(lines)

    used_by, tested_by = reverse_index if reverse_index is not None else build_reverse_index(repo_root)

    for pf in py_files:
        info = inspect_python_file(pf, repo_root)
        rel_p = info["rel_path"]
        lines.append(f"#### `{rel_p}`")
        if info.get("error"):
            lines.append(f"- **Syntax Error:** `{info['error']}`")
            lines.append("")
            continue
        if info["internal_imports"]:
            lines.append("- **Internal Imports:** " + ", ".join(f"`{imp}`" for imp in info["internal_imports"]))
        if used_by.get(rel_p):
            lines.append("- **Used By:** " + _format_capped_refs(used_by[rel_p]))
        if tested_by.get(rel_p):
            lines.append("- **Tested By:** " + _format_capped_refs(tested_by[rel_p]))
        if info["constants"]:
            lines.append("- **Constants:** " + ", ".join(f"`{c}`" for c in info["constants"]))
        for cls in info["classes"]:
            kind = "Dataclass" if cls["is_dataclass"] else "Class"
            base_str = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
            lines.append(f"- **{kind} `{cls['name']}{base_str}`**")
            if cls["fields"]:
                lines.append("  - Fields: " + ", ".join(f"`{f}`" for f in cls["fields"]))
            if cls["methods"]:
                for m in cls["methods"]:
                    lines.append(f"  - Method: `{m}`")
        for fn in info["functions"]:
            lines.append(f"- **Function:** `{fn}`")
        lines.append("")

    for tf in poly_files:
        t_info = inspect_polyglot_file(tf, repo_root)
        rel_t = t_info["rel_path"]
        lines.append(f"#### `{rel_t}`")
        if used_by.get(rel_t):
            lines.append("- **Used By:** " + _format_capped_refs(used_by[rel_t]))
        if tested_by.get(rel_t):
            lines.append("- **Tested By:** " + _format_capped_refs(tested_by[rel_t]))
        if t_info["exports"]:
            lines.append("- **Exports:** " + ", ".join(f"`{e}`" for e in t_info["exports"]))
        lines.append("")

    if lines[-1] == "":
        lines.pop()
    lines.append(END_MARKER)
    return "\n".join(lines)


def sync_agents_file(
    agents_md: Path,
    repo_root: Path,
    reverse_index: Optional[tuple[dict[str, list[str]], dict[str, list[str]]]] = None,
) -> bool:
    content = agents_md.read_text(encoding="utf-8")
    start_m = re.search(rf"^[ \t]*{re.escape(START_MARKER)}[ \t]*$", content, re.M)
    end_m = re.search(rf"^[ \t]*{re.escape(END_MARKER)}[ \t]*$", content, re.M)
    if not start_m or not end_m or end_m.start() <= start_m.end():
        return False

    is_root = (agents_md.parent.resolve() == repo_root.resolve())
    new_block = (
        build_root_block(repo_root)
        if is_root
        else build_directory_block(agents_md.parent, repo_root, reverse_index=reverse_index)
    )

    updated = content[:start_m.start()] + new_block + content[end_m.end():]

    if updated != content:
        agents_md.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_workspace(workspace_root: Path) -> list[str]:
    if not workspace_root.exists():
        return []
    modified = []
    agents_files = sorted(
        p for p in workspace_root.rglob("AGENTS.md")
        if not any(part in IGNORE_DIRS for part in p.relative_to(workspace_root).parts)
    )
    non_root = [p for p in agents_files if p.parent.resolve() != workspace_root.resolve()]
    root_files = [p for p in agents_files if p.parent.resolve() == workspace_root.resolve()]
    reverse_index = build_reverse_index(workspace_root) if non_root else ({}, {})
    for af in non_root + root_files:
        if sync_agents_file(af, workspace_root, reverse_index=reverse_index):
            modified.append(af.relative_to(workspace_root).as_posix())
    # Second pass on root if non_root changed line counts
    for af in root_files:
        if sync_agents_file(af, workspace_root, reverse_index=reverse_index) and af.relative_to(workspace_root).as_posix() not in modified:
            modified.append(af.relative_to(workspace_root).as_posix())
    return modified


def main():
    parser = argparse.ArgumentParser(description="Sync AGENTS.md auto-generated contract blocks or print a target Context Capsule.")
    parser.add_argument("--workspace", default=".", help="Workspace root directory")
    parser.add_argument(
        "--for",
        dest="target_file",
        default="",
        help="Print single-hop Context Capsule (Why/ADRs, callers, tests, contract) for a target file and mark session context ledger",
    )
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.target_file:
        print(build_context_capsule(root, args.target_file, mark_ledger=True))
        return
    changed = sync_workspace(root)
    if changed:
        print(f"[ai-native-repo] Updated {len(changed)} AGENTS.md file(s): {', '.join(changed)}")
    else:
        print("[ai-native-repo] All AGENTS.md contracts are up to date.")


if __name__ == "__main__":
    main()
