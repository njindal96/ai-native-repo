---
name: init-ai-native-repo
description: Bootstraps or updates a repository with AI-Native v2.0 best practices: hierarchical AGENTS.md (<200 lines) with Always/Ask First/Never tiers, .ai-native.json import boundaries, machine-readable ADRs (docs/adr/), AST contract sync, deterministic verification, and Git pre-commit hook.
---

# Initialize AI-Native Repository (v2.0)

Use this skill when bootstrapping or auditing a repository designed for autonomous AI coding agents.

## Workflow

1. **Run the Automated 1-Command Bootstrapper:**
   Run the plugin's automated bootstrap script on the target repository root:
   ```bash
   python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace .
   ```
   This automatically creates/updates:
   - `scripts/sync_context.py` and `scripts/verify_architecture.py`
   - `.ai-native.json` (auto-detected code layers & test command)
   - Root `AGENTS.md` + `CLAUDE.md` symlink + scoped `*/AGENTS.md` files with `<!-- AUTO-GENERATED -->` markers
   - Starter `docs/adr/001-ai-native-architecture.md`
   - `.githooks/pre-commit` + `git config core.hooksPath .githooks`

2. **Customize Domain Boundaries & Invariants:**
   - **`.ai-native.json`**: Tighten `allowed_internal_prefixes` and `forbidden_imports` for each architectural layer, and set `test_command`.
   - **Root & Scoped `AGENTS.md`** (<200 lines each): Document system overview, schema/API invariants, and `Always / Ask First / Never` action tiers. Keep detailed runbooks in `.agents/skills/<role>/SKILL.md`.
   - **`docs/adr/NNN-slug.md`**: Add machine-readable ADRs with YAML frontmatter (`id`, `title`, `status`, `applies_to`, `summary`) for non-obvious constraints so `sync_context.py` routes each ADR into matching scoped `AGENTS.md` files.

3. **Verify Closed-Loop Sync:**
   ```bash
   python3 scripts/sync_context.py && python3 scripts/verify_architecture.py
   ```
   Confirm all `<!-- AUTO-GENERATED -->` blocks are populated, context budgets pass, import boundaries hold, and offline unit tests pass.
