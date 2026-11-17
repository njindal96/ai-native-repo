# AGENTS.md — `ai-native-repo`

> **AI Agent Quickstart (If a user points you at this repo to make their project AI-native):**
> 1. Install this plugin globally: `curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash` (or `git clone https://github.com/njindal96/ai-native-repo ~/.gemini/config/plugins/ai-native-repo`).
> 2. Bootstrap the user's target repository automatically:
>    `python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace /path/to/target-repo`
> 3. Customize `.ai-native.json` layer boundaries, `AGENTS.md` domain invariants, and `docs/adr/` entries for the target project.

## 1. Repository Overview & Zero-Dependency Contract
- **Purpose:** Reusable Antigravity / Claude Code / Gemini CLI plugin + standalone CLI scripts that keep `AGENTS.md` contracts auto-synced via AST introspection (`sync_context.py`), enforce import boundaries and context budgets (`.ai-native.json` + `verify_architecture.py`), and block semantic drift (`stop_gate_hook.py`).
- **Zero-Dependency Rule:** All Python scripts under `scripts/` MUST use **Python 3 standard library only** (`ast`, `json`, `pathlib`, `py_compile`, `subprocess`, `shutil`). Never add third-party PyPI requirements.
- **Cross-Agent Hook Compatibility:** `post_tool_hook.py` and `stop_gate_hook.py` must support both Antigravity (`workspacePaths`, `terminationReason`, `decision: allow/continue`) and Claude Code (`cwd`, `stop_hook_active`, `decision: block` / `{}`).

## 2. Verification & Operational Tiers
- **Sync & Verify:** `python3 scripts/sync_context.py && python3 scripts/verify_architecture.py`
- **Always:** Keep `scripts/sync_context.py` and `scripts/verify_architecture.py` standalone and copy-safe (no relative imports between them; `bootstrap_repo.py` copies both into target repos).
- **Ask First:** Changing `.ai-native.json` schema keys or `<!-- AUTO-GENERATED:START -->` / `<!-- AUTO-GENERATED:END -->` marker format.
- **Never:** Add external dependencies to `scripts/`; exceed 200 lines in `AGENTS.md`.

<!-- AUTO-GENERATED:START -->
### Auto-Generated Repository & Context Map
_Updated automatically from source code & ADRs by `sync_context.py`. Do not edit inside this block._

#### Scoped `AGENTS.md` Hierarchy
- `AGENTS.md` — Scope: Repository Root (34 lines)
- `rules/AGENTS.md` — Scope: `rules/` (13 lines)

#### Codebase Module & Dependency Summary
- `scripts/bootstrap_repo.py` — Functions: `make_executable()`, `detect_code_layers()`, `detect_test_command()`, `ensure_markers_in_agents_md()`, `bootstrap_repository()`, `main()` | Constants: `PLUGIN_SCRIPTS_DIR`
- `scripts/post_tool_hook.py` — Functions: `main()`
- `scripts/stop_gate_hook.py` — Functions: `split_semantic_and_auto()`, `check_semantic_drift()`, `format_response()`, `main()`
- `scripts/sync_context.py` — Functions: `format_annotation()`, `is_internal_module()`, `parse_adr_frontmatter()`, `load_all_adrs()`, `inspect_python_file()`, `inspect_ts_file()`, `build_root_block()`, `build_directory_block()`, `sync_agents_file()`, `sync_workspace()`, `main()` | Constants: `START_MARKER`, `END_MARKER`, `IGNORE_DIRS`
- `scripts/verify_architecture.py` — Functions: `extract_imports()`, `is_internal_module()`, `check_llm_smells()`, `matches_prefix()`, `verify_workspace()`, `main()` | Constants: `IGNORE_DIRS`
<!-- AUTO-GENERATED:END -->
