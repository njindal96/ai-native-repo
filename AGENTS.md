# AGENTS.md — `ai-native-repo`

> **AI Agent Quickstart (If a user points you at this repo to make their project AI-native):**
> 1. Install this plugin globally: `curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash`.
> 2. Bootstrap the user's target repository automatically:
>    `python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace /path/to/target-repo`
> 3. Customize `.ai-native.json` layer boundaries, `AGENTS.md` domain invariants, and `docs/adr/` entries for the target project.

## 1. Repository Overview & Zero-Dependency Contract (v3.1 Hardened)
- **Purpose:** Reusable cross-agent plugin (`hooks.json`, `rules/`, `skills/`) + standalone scripts (`sync_context.py`, `verify_architecture.py`, `bootstrap_repo.py`, `pre_tool_hook.py`, `post_tool_hook.py`, `stop_gate_hook.py`) implementing Zero-Collateral Agentic Engineering.
- **Zero-Dependency Rule:** All Python scripts under `scripts/` MUST use **Python 3 standard library only** (`ast`, `json`, `pathlib`, `shlex`, `tempfile`, `py_compile`, `subprocess`, `shutil`) and pass under `python3 -I` (isolated mode).
- **Hardened Guarantees (v3.2):**
  - **Pre-Edit Context Gate & Single-Hop Context Capsule (`--for`):** `sync_context.py` indexes `Used By` callers and `Tested By` test functions across `AGENTS.md`, and generates 1-hop Context Capsules (`python3 scripts/sync_context.py --for <file>`) bundling governing `AGENTS.md`, ADRs, blast radius, and tests. `pre_tool_hook.py` tracks reads of `AGENTS.md`/`docs/adr/*.md` and blocks blind edits to layer files once to inject the missing Context Capsule.
  - **ADR Rationale Enforcement (`check_adr_completeness`):** `verify_architecture.py` requires every `docs/adr/*.md` to contain `Context / Why` and `Decision & Invariants / Anti-Patterns` headings.
  - **Isolated Mutation & Timeouts:** `run_diff_mutation_check()` copies the workspace into a `TemporaryDirectory` (never mutating working-tree files in-place) with dynamic per-mutant timeouts (`TimeoutExpired` = killed) and configurable `max_mutants`.
  - **Tokenized Shell & Guardrail Tamper Protection:** `pre_tool_hook.py` uses `shlex`-based compound parsing to block `-n`/`--no-verify` (including `-amn`), `core.hooksPath` overrides, and `chmod -x .githooks/*`; `verify_architecture.py` blocks `.ai-native.json` weakening and `.githooks/*` tampering vs `HEAD`.
  - **Resilient Introspection & Cross-Agent Hooks:** `sync_context.py` supports CRLF/YAML lists in ADRs (`adr_matches_directory`, `adr_matches_path`) and comment/string-stripped polyglot exports (`.ts`/`.js`/`.go`/`.rs`/`.java`); `verify_architecture.py` resolves relative imports, `__init__.py` re-exports, inherited `@dataclass` fields (`init=False`/`KW_ONLY` aware), and module aliases; `stop_gate_hook.py` normalizes prose whitespace (`normalize_semantic_prose`) and scopes drift to public contracts; `bootstrap_repo.py` chains pre-existing `.git/hooks/*`, merges `CLAUDE.md`, and supports non-destructive JSON hook merging.

## 2. Verification & Operational Tiers
- **Isolated Tests & Self-Verify:** `python3 -I -m unittest discover -s tests -p 'test_*.py' && python3 scripts/sync_context.py && python3 scripts/verify_architecture.py`
- **Always:** Keep `scripts/sync_context.py` and `scripts/verify_architecture.py` standalone and copy-safe; maintain regression coverage in `tests/test_ai_native_engine.py`; commit with `.githooks` enabled (`Never --no-verify`).
- **Ask First:** Changing `.ai-native.json` schema keys or `<!-- AUTO-GENERATED:START -->` / `<!-- AUTO-GENERATED:END -->` marker format.
- **Never:** Add external dependencies to `scripts/`; exceed 200 lines in `AGENTS.md`.

<!-- AUTO-GENERATED:START -->
### Auto-Generated Repository & Context Map
_Updated automatically from source code & ADRs by `sync_context.py`. Do not edit inside this block._

#### Scoped `AGENTS.md` Hierarchy
- `AGENTS.md` — Scope: Repository Root (44 lines)
- `rules/AGENTS.md` — Scope: `rules/` (14 lines)

#### Active Architecture Decision Records (`docs/adr/`)
- **`ADR-001`** (`docs/adr/001-six-pillar-zero-collateral-verification.md`) — **Six-Pillar Zero-Collateral Verification Harness** [Applies to: `*`] — Enforce PreToolUse hook-bypass blocking, cross-module AST symbol resolution, anti-test-tampering & contract compatibility checks vs HEAD, domain invariants, diff-scoped AST micro-mutation testing, and semantic drift gating using Python stdlib only.

#### Codebase Module & Dependency Summary
- `scripts/bootstrap_repo.py` — Functions: `make_executable()`, `detect_code_layers()`, `detect_test_command()`, `ensure_markers_in_agents_md()`, `merge_hooks_into_settings()`, `bootstrap_repository()`, `main()` | Constants: `_SCRIPT_DIR`, `PLUGIN_SCRIPTS_DIR`, `PLUGIN_ROOT_DIR`, `ALL_CODE_EXTENSIONS`
- `scripts/post_tool_hook.py` — Functions: `extract_workspaces()`, `main()` | Constants: `_SCRIPT_DIR`
- `scripts/pre_tool_hook.py` — Functions: `extract_command()`, `format_response()`, `split_compound_commands()`, `validate_shell_command()`, `resolve_git_target()`, `extract_file_target()`, `resolve_workspace_and_rel()`, `main()` | Constants: `_SCRIPT_DIR`, `SHELL_TOOL_NAMES`, `READ_TOOL_NAMES`, `EDIT_TOOL_NAMES`, `GIT_HOOKED_SUBCOMMANDS`, `GIT_GLOBAL_VALUE_FLAGS`, `GIT_SUBCMD_VALUE_FLAGS`, `WRAPPERS`
- `scripts/stop_gate_hook.py` — Functions: `split_semantic_and_auto()`, `normalize_semantic_prose()`, `extract_public_contract_lines()`, `check_semantic_drift()`, `format_response()`, `extract_workspaces()`, `main()` | Constants: `_SCRIPT_DIR`
- `scripts/sync_context.py` — Functions: `format_annotation()`, `resolve_relative_import()`, `is_internal_module()`, `parse_adr_frontmatter()`, `adr_matches_directory()`, `adr_matches_path()`, `build_reverse_index()`, `get_context_ledger_path()`, `record_context_read()`, `has_context_for_target()`, `build_context_capsule()`, `load_all_adrs()`, `inspect_python_file()`, `strip_comments_and_strings()`, `inspect_polyglot_file()`, `inspect_ts_file()`, `build_root_block()`, `build_directory_block()`, `sync_agents_file()`, `sync_workspace()`, `main()` | Constants: `START_MARKER`, `END_MARKER`, `IGNORE_DIRS`, `POLYGLOT_EXTENSIONS`
- `scripts/verify_architecture.py` — Functions: `resolve_import_module()`, `extract_polyglot_imports()`, `extract_imports()`, `is_internal_module()`, `check_llm_smells()`, `matches_prefix()`, `check_symbol_and_dataclass_resolution()`, `check_git_head_regressions()`, `run_diff_mutation_check()`, `check_adr_completeness()`, `verify_workspace()`, `main()` | Constants: `IGNORE_DIRS`, `POLYGLOT_EXTENSIONS`, `NON_FIELD_BASE_NAMES`, `SECRET_PATTERNS`
- `tests/test_ai_native_engine.py` — Classes: `TestAiNativeEngine` | Constants: `REPO_ROOT`, `SCRIPTS_DIR`
<!-- AUTO-GENERATED:END -->
