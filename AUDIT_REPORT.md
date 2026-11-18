# Silent Gaps Audit & Remediation Report (`ai-native-repo` v3.1)

Independent validation, triage, code remediation, and regression testing across all 8 candidate silent gaps (`GAP-01` through `GAP-08`) while preserving the **Zero External Dependencies (Pure Python 3 Stdlib Only)** constraint (`python3 -I` isolated mode verified).

## Executive Summary

| Gap ID | Status | Triage Decision | Files Modified | Tests Added | Final Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **GAP-01** | CONFIRMED | `FIX_IN_CODE` | `scripts/verify_architecture.py`, `scripts/bootstrap_repo.py` | `test_gap01_mutation_isolation_crash_safety_and_infinite_loop_timeout` | PASS |
| **GAP-02** | CONFIRMED | `FIX_IN_CODE` | `scripts/pre_tool_hook.py`, `scripts/verify_architecture.py` | `test_gap02_pre_tool_shell_gate_bypasses_and_config_tampering` | PASS |
| **GAP-03** | CONFIRMED | `FIX_IN_CODE` + `SCOPE_IN_DOCS` | `scripts/sync_context.py`, `scripts/verify_architecture.py`, `README.md` | `test_gap03_polyglot_comment_stripping_multiline_and_boundary_linting` | PASS |
| **GAP-04** | CONFIRMED | `FIX_IN_CODE` | `scripts/verify_architecture.py`, `scripts/sync_context.py` | `test_gap04_python_resolver_inheritance_init_false_relative_imports_reexports_and_aliases` | PASS |
| **GAP-05** | CONFIRMED | `FIX_IN_CODE` | `scripts/sync_context.py` | `test_gap05_adr_frontmatter_crlf_body_dividers_colons_and_lists` | PASS |
| **GAP-06** | CONFIRMED | `FIX_IN_CODE` | `scripts/stop_gate_hook.py` | `test_gap06_stop_gate_whitespace_bypass_private_edits_and_reentry` | PASS |
| **GAP-07** | CONFIRMED | `FIX_IN_CODE` | `scripts/bootstrap_repo.py`, `install.sh`, `README.md` | `test_gap07_non_destructive_onboarding_preserves_existing_hooks_claude_md_and_settings` | PASS |
| **GAP-08** | CONFIRMED | `FIX_IN_CODE` + `SCOPE_IN_DOCS` | `scripts/pre_tool_hook.py`, `scripts/post_tool_hook.py`, `scripts/stop_gate_hook.py`, `hooks.json`, `README.md` | `test_gap08_multi_agent_hook_payload_resilience` | PASS |

---

## Detailed Gap Findings & Remediations

### GAP-01: Working-Tree Corruption & Infinite Hang in AST Micro-Mutation Testing
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE`
- **Before-Fix Evidence:**
  - `scripts/verify_architecture.py:362`: `def run_diff_mutation_check(workspace_root: Path, test_cmd: str, max_mutants: int = 3)` hardcoded `max_mutants=3` instead of reading `max_mutants` from `.ai-native.json`.
  - `scripts/verify_architecture.py:443`: `py_path.write_text(mut_text, encoding="utf-8")` mutated files **in-place** inside `workspace_root` (unsafe under `SIGKILL`/`SIGTERM`).
  - `scripts/verify_architecture.py:444-451`: `subprocess.run(..., timeout=30)` lacked dynamic scaling and did not catch `subprocess.TimeoutExpired`, causing an unhandled exception on infinite-loop mutants.
  - Probe reproduction confirmed mutants ran directly inside live `workspace_root` (`GAP-01 run_diff_mutation_check errs -> ['[Weak Test / Surviving Mutant] mod.py:2 — inverting \`x == 2\` -> \`(x != 2)\` did not fail any test.']`).
- **Root Cause:** Mutants were applied directly to user workspace files and executed with a rigid `30s` timeout without catching `TimeoutExpired`.
- **Remediation Applied:**
  - Copied the workspace into an isolated `tempfile.TemporaryDirectory()` (excluding `.git`, `__pycache__`, `node_modules`, `.next`, `.venv`, `venv`, `dist`, `build`) before writing or running any mutant, guaranteeing 0 bytes ever change in the live working tree.
  - Measured baseline test duration in `verify_workspace()` and enforced `timeout = max(5.0, baseline_duration * 3.0)` (or explicit `timeout_sec`), treating `subprocess.TimeoutExpired` as a cleanly killed mutant.
  - Added configurable `max_mutants` support (default `25`) in `verify_architecture.py` and `bootstrap_repo.py`.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap01_mutation_isolation_crash_safety_and_infinite_loop_timeout` (asserts SHA-256 byte-identity of live workspace files, isolated `cwd`, infinite-loop `while stop_flag == False` timeout handling, and `max_mutants` cap).

---

### GAP-02: Shell Gate (`PreToolUse`) Flag & Git Config Bypasses
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE`
- **Before-Fix Evidence:**
  - `scripts/pre_tool_hook.py:71`: used naive regex `re.search(r"(^|\s)--no-verify(\s|$)", cmd) or re.search(r"\bgit\s+commit\b[^;&|]*\s-n(\s|$)", cmd)`.
  - Probe output:
    - `git commit -amn "msg" -> {"decision": "allow"}`
    - `git -c core.hooksPath=/dev/null commit -m "x" -> {"decision": "allow"}`
    - `chmod -x .githooks/pre-commit -> {"decision": "allow"}`
    - `git merge -n dev -> {"decision": "allow"}`
  - `scripts/verify_architecture.py:234-330` did not verify `.ai-native.json` or `.githooks/*` against `HEAD`.
- **Root Cause:** Substring/regex inspection failed to tokenize compound shell commands, bundled short flags (`-amn`), global `-c core.hooksPath` overrides, or guardrail tampering, while also causing false positives across newlines.
- **Remediation Applied:**
  - Replaced regex matching with `split_compound_commands()` (`shlex.shlex` splitting on `&&`, `||`, `;`, `|`, `&`, `\n`) and `validate_shell_command()` in `scripts/pre_tool_hook.py`.
  - Blocked `--no-verify` and `-n` (including bundled flags like `-amn`/`-anm`) across `git commit`, `push`, `merge`, `cherry-pick`, and `rebase`, while allowing legitimate commands (`git clean -n`, `git log -n 5`, `-n` inside commit message strings).
  - Blocked `git -c core.hooksPath=...`, `git config core.hooksPath` tampering, and `chmod -x`/`rm`/`mv` on `.githooks/*` and `.ai-native.json`.
  - Added `_check_config_and_hooks_tampering()` in `scripts/verify_architecture.py` to block weakening `mutation_testing`, `enforce_contract_compatibility`, `context_budget`, `max_layers_touched`, `forbidden_imports`, or hook executable bits vs `HEAD`.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap02_pre_tool_shell_gate_bypasses_and_config_tampering` (16 blocked bypass vectors + 8 allowed legitimate commands + `.ai-native.json`/`.githooks` tamper checks).

---

### GAP-03: "Polyglot AST" Claim vs. Pure Python `ast` Stdlib Reality
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE` + `SCOPE_IN_DOCS`
- **Before-Fix Evidence:**
  - `scripts/sync_context.py:159-168` only inspected `.ts`/`.tsx` via raw-text regex (`line 164`), and `scripts/verify_architecture.py:491-515` only checked Python imports.
  - Probe output on `/* export function fakeCommented() {} */`:
    `GAP-03 inspect_ts_file -> {'rel_path': 'demo.ts', 'exports': ['fakeCommented', 'realFn']}`.
  - `README.md:93` advertised "Portable polyglot AST" without documenting Tier 1 vs Tier 2 capabilities.
- **Root Cause:** Regex ran on unstripped source text containing comments/strings, non-Python files beyond `.ts/.tsx` were skipped, and docs overstated full AST support for non-Python languages.
- **Remediation Applied:**
  - Added pure-stdlib single-pass comment & string stripper `strip_comments_and_strings()` in `scripts/sync_context.py` and `inspect_polyglot_file()` supporting multiline/generic TypeScript/JS (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`), Go (`.go`), Rust (`.rs`), and Java (`.java`).
  - Added `_strip_comments_keep_strings()` and `extract_polyglot_imports()` in `scripts/verify_architecture.py` to enforce layer `forbidden_imports` and `max_layers_touched` across polyglot files.
  - Added an honest **Language Support Tiers** table in `README.md` separating Tier 1 Python AST checks from Tier 2 lexical polyglot checks.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap03_polyglot_comment_stripping_multiline_and_boundary_linting`.

---

### GAP-04: Python Cross-Module Hallucination & Dataclass Resolver Blind Spots
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE`
- **Before-Fix Evidence:**
  - `scripts/verify_architecture.py:41-42, 112-200` and `scripts/sync_context.py:104-107`: ignored relative `ImportFrom` (`node.level > 0`), ignored `@dataclass` inheritance, ignored `field(init=False)` / `KW_ONLY` / `ClassVar`, did not propagate `__init__.py` re-exports, and did not verify `import mod as alias; alias.attr`.
  - Probe output on `from .models import Child`, `m.does_not_exist()`, and `Child(x=1, y=2)` (`y: int = field(init=False)`): `GAP-04 -> []` (missed both errors completely).
- **Root Cause:** Symbol resolution indexed only direct top-level definitions and absolute non-aliased imports.
- **Remediation Applied:**
  - Implemented `resolve_import_module()` / `resolve_relative_import()` for `level > 0` imports and updated `extract_imports()`, `inspect_python_file()`, and `check_symbol_and_dataclass_resolution()`.
  - Added fixed-point re-export propagation across `__init__.py` and `from ... import *`.
  - Resolved same-repo `@dataclass` inheritance hierarchies cleanly while gracefully skipping keyword checks when inheriting from external unindexed base classes (zero false positives).
  - Respected `field(init=False)`, `KW_ONLY`, `ClassVar`, `@dataclass(init=False)`, and custom `__init__`.
  - Added module-alias verification (`import internal.mod as alias; alias.attr`) in `check_symbol_and_dataclass_resolution()`.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap04_python_resolver_inheritance_init_false_relative_imports_reexports_and_aliases`.

---

### GAP-05: Custom Stdlib YAML Frontmatter Parser Fragility in ADR Router
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE`
- **Before-Fix Evidence:**
  - `scripts/sync_context.py:56-67` split on `text.split("---", 2)` and parsed `applies_to` as a scalar string only.
  - Probe output on CRLF ADR with YAML list `applies_to` and body `---`:
    `GAP-05 -> {'rel_path': 'docs/adr/002-test.md', 'id': 'ADR-002', 'title': 'ADR-02: Retry Policy', 'status': 'accepted', 'applies_to': '', 'summary': 'Keep retries: idempotent'}` (`applies_to` lost completely!).
- **Root Cause:** Frontmatter delimiter detection wasn't CRLF-normalized or line-anchored, and multi-line / inline YAML lists weren't parsed.
- **Remediation Applied:**
  - Normalized `\r\n` / `\r` and BOM, anchored opening `---` on line 1, and matched only the first closing `\n---\n` delimiter so body `---` horizontal rules cannot interfere.
  - Preserved colons inside quoted strings via `_strip_matching_quotes()`.
  - Added support for inline `[a, b]`, comma-separated, and indented `- item` YAML lists for `applies_to`, routed via `adr_matches_directory()`.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap05_adr_frontmatter_crlf_body_dividers_colons_and_lists`.

---

### GAP-06: Stop Gate (`stop_gate_hook.py`) Whitespace Bypass & Infinite Refusal Loops
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE`
- **Before-Fix Evidence:**
  - `scripts/stop_gate_hook.py:19, 60`: compared `(before + "\n" + after).strip()` with raw equality (allowing a single added blank line `\n` inside semantic prose to register as a semantic update: probe `GAP-06 internal blank-line equal? -> False`), compared full raw `auto_body`, and omitted changed contract symbols in refusal messages.
- **Root Cause:** Lack of internal whitespace collapsing on semantic prose and lack of public-contract filtering on auto-generated blocks.
- **Remediation Applied:**
  - Added `normalize_semantic_prose()` (`re.sub(r"\s+", " ", text).strip()`) so blank-line or spacing edits outside `<!-- AUTO-GENERATED -->` cannot bypass the drift gate.
  - Added `extract_public_contract_lines()` so private helper edits or formatting changes do not trigger false-positive drift refusals.
  - Included exact changed contract lines in the refusal message (`rel_path (changed contract: ...)`).
  - Enforced safe early exit on `stop_hook_active` and `continuationActive` to prevent infinite refusal loops.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap06_stop_gate_whitespace_bypass_private_edits_and_reentry`.

---

### GAP-07: Non-Destructive Onboarding Claims (`install.sh` & `bootstrap_repo.py`)
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE`
- **Before-Fix Evidence:**
  - `scripts/bootstrap_repo.py:151-155, 203-204`: set `git config core.hooksPath .githooks` without preserving `.git/hooks/pre-commit`, left custom non-symlink `CLAUDE.md` unmerged, and called `symlink_to` without a Windows `OSError` fallback.
  - Probe output: `GAP-07 core.hooksPath -> .githooks`, `GAP-07 CLAUDE.md preserved in AGENTS.md? -> False`, `GAP-07 old hook chained in .githooks? -> False`.
- **Root Cause:** Onboarding assumed an empty hook/doc baseline rather than migrating existing hooks and `CLAUDE.md` files.
- **Remediation Applied:**
  - Added `_merge_claude_md_into_agents_md()` to merge custom `CLAUDE.md` prose into `AGENTS.md` before symlinking, with automatic `shutil.copy2` fallback on `OSError`.
  - Added `_install_git_hooks_non_destructive()` to preserve pre-existing `.git/hooks/*` or custom `core.hooksPath` scripts into `.githooks/<hook>.local` and execute them prior to `sync_context.py` / `verify_architecture.py`.
  - Added `merge_hooks_into_settings()` and `install.sh --merge-settings <json>` to merge hooks into existing agent JSON configs non-destructively using stdlib `json`.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap07_non_destructive_onboarding_preserves_existing_hooks_claude_md_and_settings`.

---

### GAP-08: Multi-Agent Hook Schema & Compatibility Parity
- **Status:** `CONFIRMED` | **Triage:** `FIX_IN_CODE` + `SCOPE_IN_DOCS`
- **Before-Fix Evidence:**
  - `scripts/pre_tool_hook.py:60-68`, `scripts/post_tool_hook.py:8-18`, `scripts/stop_gate_hook.py:77-96`, `hooks.json:1-35`:
    - Probe output under `"null"` stdin: `pre_tool_hook.py` (`TypeError: argument of type 'NoneType' is not iterable`), `post_tool_hook.py` (`AttributeError: 'NoneType' object has no attribute 'get'`), `stop_gate_hook.py` (`TypeError: argument of type 'NoneType' is not iterable`).
    - Probe output under `python3 -I` isolated mode: `ModuleNotFoundError: No module named 'sync_context'` across all 3 hook scripts.
    - Stop payloads lacking both `terminationReason` and `stop_hook_active` exited without running verification.
- **Root Cause:** Top-level sibling imports omitted `sys.path` bootstrapping under `python3 -I`, payload parsing assumed dict shapes, and `hooks.json`/`README.md` lacked cross-agent parity documentation.
- **Remediation Applied:**
  - Added explicit `_SCRIPT_DIR` `sys.path` insertion across all scripts so `python3 -I` (and `PYTHONSAFEPATH=1`) succeeds cleanly.
  - Hardened stdin/payload extraction (`extract_command`, `extract_workspaces`) against `null`, arrays, malformed JSON, and non-dict fields across Antigravity, Claude Code, Gemini CLI, Cursor, and Codex shapes.
  - Fixed `stop_gate_hook.py` to run verification whenever `terminationReason` is absent and neither `stop_hook_active` nor `continuationActive` is true.
  - Expanded `hooks.json` matchers and added the **Supported Agent Integration Matrix** table in `README.md`.
- **Regression Test Added:** `tests/test_ai_native_engine.py::TestAiNativeEngine.test_gap08_multi_agent_hook_payload_resilience`.
