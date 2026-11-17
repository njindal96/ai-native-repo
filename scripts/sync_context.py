#!/usr/bin/env python3
"""
Polyglot AST & Repository Introspector (v2.0)
Updates <!-- AUTO-GENERATED:START --> ... <!-- AUTO-GENERATED:END --> blocks
across all AGENTS.md files with:
- Python (.py) classes, dataclasses, functions, constants, and internal imports
- TypeScript/JS (.ts, .tsx) exported types, interfaces, functions, and routes
- Machine-readable ADR index (from docs/adr/*.md YAML frontmatter)
- Context budget telemetry (line count)
"""
import argparse
import ast
import fnmatch
import re
from pathlib import Path

START_MARKER = "<!-- AUTO-GENERATED:START -->"
END_MARKER = "<!-- AUTO-GENERATED:END -->"
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".next", ".venv", "venv",
    ".idea", ".vscode", ".agents", ".githooks", "dist", "build"
}


def format_annotation(node) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def is_internal_module(mod_name: str, repo_root: Path) -> bool:
    top = mod_name.split(".")[0]
    if not top or top in {"tests", "scripts", "docs"}:
        return False
    return (repo_root / top).is_dir() or (repo_root / f"{top}.py").is_file()


def parse_adr_frontmatter(adr_path: Path, repo_root: Path) -> dict:
    rel_path = adr_path.relative_to(repo_root).as_posix()
    meta = {
        "rel_path": rel_path,
        "id": adr_path.stem.split("-")[0].upper(),
        "title": adr_path.stem,
        "status": "accepted",
        "applies_to": "*",
        "summary": "",
    }
    try:
        text = adr_path.read_text(encoding="utf-8")
    except Exception:
        return meta

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            for line in fm.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip('"').strip("'")
                    if k in meta:
                        meta[k] = v
    return meta


def load_all_adrs(repo_root: Path) -> list[dict]:
    adr_dir = repo_root / "docs" / "adr"
    if not adr_dir.exists():
        return []
    return [
        parse_adr_frontmatter(p, repo_root)
        for p in sorted(adr_dir.glob("*.md"))
        if p.name.lower() != "readme.md"
    ]


def inspect_python_file(py_path: Path, rel_to: Path) -> dict:
    rel_path = py_path.relative_to(rel_to).as_posix()
    info = {
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

    seen_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_internal_module(alias.name, rel_to) and alias.name not in seen_imports:
                    seen_imports.add(alias.name)
                    info["internal_imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if is_internal_module(node.module, rel_to) and node.module not in seen_imports:
                seen_imports.add(node.module)
                info["internal_imports"].append(node.module)

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
                elif isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    args = [
                        f"{a.arg}: {format_annotation(a.annotation)}" if a.annotation else a.arg
                        for a in item.args.args
                        if a.arg != "self"
                    ]
                    ret = format_annotation(item.returns)
                    sig = f"{item.name}({', '.join(args)})" + (f" -> {ret}" if ret else "")
                    methods.append(sig)
            info["classes"].append({
                "name": node.name,
                "bases": bases,
                "is_dataclass": is_dataclass,
                "fields": fields,
                "methods": methods,
            })
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            args = [
                f"{a.arg}: {format_annotation(a.annotation)}" if a.annotation else a.arg
                for a in node.args.args
            ]
            ret = format_annotation(node.returns)
            sig = f"{node.name}({', '.join(args)})" + (f" -> {ret}" if ret else "")
            info["functions"].append(sig)

    return info


def inspect_ts_file(ts_path: Path, rel_to: Path) -> dict:
    rel_path = ts_path.relative_to(rel_to).as_posix()
    exports = []
    try:
        text = ts_path.read_text(encoding="utf-8")
        for m in re.finditer(r"export\s+(?:async\s+)?(?:function|const|class|interface|type)\s+([A-Za-z0-9_]+)", text):
            exports.append(m.group(1))
    except Exception:
        pass
    return {"rel_path": rel_path, "exports": exports}


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

    ts_files = sorted(
        p for p in repo_root.rglob("*.ts*")
        if not any(part in IGNORE_DIRS for part in p.relative_to(repo_root).parts)
        and p.suffix in {".ts", ".tsx"}
    )
    for tf in ts_files:
        t_info = inspect_ts_file(tf, repo_root)
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
    matching_adrs = [
        a for a in adrs
        if fnmatch.fnmatch(rel_dir, a["applies_to"].rstrip("/*"))
        or fnmatch.fnmatch(f"{rel_dir}/", a["applies_to"])
        or a["applies_to"].startswith(rel_dir)
        or a["applies_to"] == "*"
    ]
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
    ts_files = sorted(
        p for p in dir_path.rglob("*.ts*")
        if not any(part in IGNORE_DIRS for part in p.relative_to(dir_path).parts)
        and p.suffix in {".ts", ".tsx"}
    )

    if not py_files and not ts_files:
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

    for tf in ts_files:
        t_info = inspect_ts_file(tf, repo_root)
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
