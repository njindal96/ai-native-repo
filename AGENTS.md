# AGENTS.md — `ai-native-repo`

> **AI Agent Quickstart (If a user points you at this repo to make their project AI-native):**
> 1. Install this plugin globally: `curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash` (or `git clone https://github.com/njindal96/ai-native-repo ~/.gemini/config/plugins/ai-native-repo`).
> 2. Bootstrap the user's target repository automatically:
>    `python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace /path/to/target-repo`
> 3. Customize `.ai-native.json` layer boundaries, `AGENTS.md` domain invariants, and `docs/adr/` entries for the target project.

## 1. Repository Overview & Zero-Dependency Contract
- **Purpose:** Reusable Antigravity / Claude Code / Gemini CLI plugin + standalone scripts implementing the v3.0 Zero-Collateral Agentic Engineering harness (`sync_context.py`, `verify_architecture.py`, `bootstrap_repo.py`, `pre_tool_hook.py`, `post_tool_hook.py`, `stop_gate_hook.py`).
- **Zero-Dependency Rule:** All Python scripts under `scripts/` MUST use **Python 3 standard library only** (`ast`, `json`, `pathlib`, `py_compile`, `subprocess`, `shutil`). Never add third-party PyPI requirements.
- **Cross-Agent Hook Compatibility:** `pre_tool_hook.py`, `post_tool_hook.py`, and `stop_gate_hook.py` support both Antigravity (`toolCall`, `workspacePaths`, `terminationReason`, `allow`/`deny`/`continue`) and Claude Code (`tool_name`, `cwd`, `stop_hook_active`, `block`/`{}`).

## 2. Verification & Operational Tiers
- **Sync & Verify:** `python3 scripts/sync_context.py && python3 scripts/verify_architecture.py`
- **Always:** Keep `scripts/sync_context.py` and `scripts/verify_architecture.py` standalone and copy-safe; maintain `tests/test_ai_native_engine.py` for every new guardrail check.
- **Ask First:** Changing `.ai-native.json` schema keys or `<!-- AUTO-GENERATED:START -->` / `<!-- AUTO-GENERATED:END -->` marker format.
- **Never:** Add external dependencies to `scripts/`; exceed 200 lines in `AGENTS.md`.

<!-- AUTO-GENERATED:START -->
### Auto-Generated Repository & Context Map
_Updated automatically from source code & ADRs by `sync_context.py`. Do not edit inside this block._

#### Scoped `AGENTS.md` Hierarchy
- `AGENTS.md` — Scope: Repository Root (39 lines)
- `rules/AGENTS.md` — Scope: `rules/` (14 lines)

#### Active Architecture Decision Records (`docs/adr/`)
- **`ADR-001`** (`docs/adr/001-six-pillar-zero-collateral-verification.md`) — **Six-Pillar Zero-Collateral Verification Harness** [Applies to: `*`] — Enforce PreToolUse hook-bypass blocking, cross-module AST symbol resolution, anti-test-tampering & contract compatibility checks vs HEAD, domain invariants, diff-scoped AST micro-mutation testing, and semantic drift gating using Python stdlib only.

#### Codebase Module & Dependency Summary
- `scripts/bootstrap_repo.py` — Functions: `make_executable()`, `detect_code_layers()`, `detect_test_command()`, `ensure_markers_in_agents_md()`, `bootstrap_repository()`, `main()` | Constants: `PLUGIN_SCRIPTS_DIR`
- `scripts/post_tool_hook.py` — Functions: `main()`
- `scripts/pre_tool_hook.py` — Functions: `extract_command()`, `format_response()`, `resolve_git_target()`, `main()`
- `scripts/stop_gate_hook.py` — Functions: `split_semantic_and_auto()`, `check_semantic_drift()`, `format_response()`, `main()`
- `scripts/sync_context.py` — Functions: `format_annotation()`, `is_internal_module()`, `parse_adr_frontmatter()`, `load_all_adrs()`, `inspect_python_file()`, `inspect_ts_file()`, `build_root_block()`, `build_directory_block()`, `sync_agents_file()`, `sync_workspace()`, `main()` | Constants: `START_MARKER`, `END_MARKER`, `IGNORE_DIRS`
- `scripts/verify_architecture.py` — Functions: `extract_imports()`, `is_internal_module()`, `check_llm_smells()`, `matches_prefix()`, `check_symbol_and_dataclass_resolution()`, `check_git_head_regressions()`, `run_diff_mutation_check()`, `verify_workspace()`, `main()` | Constants: `IGNORE_DIRS`, `SECRET_PATTERNS`
- `tests/test_ai_native_engine.py` — Classes: `TestAiNativeEngine` | Constants: `REPO_ROOT`, `SCRIPTS_DIR`
<!-- AUTO-GENERATED:END -->
