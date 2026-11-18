#!/usr/bin/env python3
"""
Polyglot Context & ADR Introspector (v3.1 — Pure Python 3 Stdlib)
Updates <!-- AUTO-GENERATED:START --> ... <!-- AUTO-GENERATED:END --> blocks
across all AGENTS.md files with:
- Tier 1 Python (.py): Full AST classes, dataclasses, async/sync functions, constants, relative/absolute imports
- Tier 2 Polyglot (.ts, .tsx, .js, .jsx, .mjs, .go, .rs, .java): Comment/string-stripped lexical exports
- Machine-readable ADR index (from docs/adr/*.md YAML frontmatter, CRLF & list safe)
- Context budget telemetry (line count)
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import re
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


def build_directory_block(dir_path: Path, repo_root: Path) -> str:
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

    for pf in py_files:
        info = inspect_python_file(pf, repo_root)
        lines.append(f"#### `{info['rel_path']}`")
        if info.get("error"):
            lines.append(f"- **Syntax Error:** `{info['error']}`")
            lines.append("")
            continue
        if info["internal_imports"]:
            lines.append("- **Internal Imports:** " + ", ".join(f"`{imp}`" for imp in info["internal_imports"]))
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
        lines.append(f"#### `{t_info['rel_path']}`")
        if t_info["exports"]:
            lines.append("- **Exports:** " + ", ".join(f"`{e}`" for e in t_info["exports"]))
        lines.append("")

    if lines[-1] == "":
        lines.pop()
    lines.append(END_MARKER)
    return "\n".join(lines)


def sync_agents_file(agents_md: Path, repo_root: Path) -> bool:
    content = agents_md.read_text(encoding="utf-8")
    start_m = re.search(rf"^[ \t]*{re.escape(START_MARKER)}[ \t]*$", content, re.M)
    end_m = re.search(rf"^[ \t]*{re.escape(END_MARKER)}[ \t]*$", content, re.M)
    if not start_m or not end_m or end_m.start() <= start_m.end():
        return False

    is_root = (agents_md.parent.resolve() == repo_root.resolve())
    new_block = build_root_block(repo_root) if is_root else build_directory_block(agents_md.parent, repo_root)

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
    for af in non_root + root_files:
        if sync_agents_file(af, workspace_root):
            modified.append(af.relative_to(workspace_root).as_posix())
    # Second pass on root if non_root changed line counts
    for af in root_files:
        if sync_agents_file(af, workspace_root) and af.relative_to(workspace_root).as_posix() not in modified:
            modified.append(af.relative_to(workspace_root).as_posix())
    return modified


def main():
    parser = argparse.ArgumentParser(description="Sync AGENTS.md auto-generated contract blocks.")
    parser.add_argument("--workspace", default=".", help="Workspace root directory")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    changed = sync_workspace(root)
    if changed:
        print(f"[ai-native-repo] Updated {len(changed)} AGENTS.md file(s): {', '.join(changed)}")
    else:
        print("[ai-native-repo] All AGENTS.md contracts are up to date.")


if __name__ == "__main__":
    main()
