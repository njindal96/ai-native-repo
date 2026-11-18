---
name: init-ai-native-repo
description: Bootstraps or updates a repository with AI-Native v3.0 best practices: hierarchical AGENTS.md (<200 lines), .ai-native.json import/contract/mutation guardrails, machine-readable ADRs (docs/adr/), AST contract sync, deterministic verification, and Git pre-commit/pre-push hooks.
---

# Initialize AI-Native Repository (v3.0)

Use this skill when bootstrapping or auditing a repository designed for autonomous AI coding agents.

## Workflow

1. **Run the Automated 1-Command Bootstrapper:**
   ```bash
   python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace .
   ```
   This automatically creates/updates:
   - `scripts/sync_context.py` and `scripts/verify_architecture.py`
   - `.ai-native.json` (with `enforce_contract_compatibility`, `mutation_testing`, `context_budget`, `test_command`, and auto-detected `layers`)
   - Root `AGENTS.md` + `CLAUDE.md` symlink + scoped `*/AGENTS.md` files with `<!-- AUTO-GENERATED -->` markers
   - Starter `docs/adr/001-ai-native-architecture.md`
   - `.githooks/pre-commit` + `.githooks/pre-push` (`git config core.hooksPath .githooks`)

2. **Customize Domain Boundaries, Invariants & ADRs:**
   - **`.ai-native.json`**: Tighten `allowed_internal_prefixes`, `forbidden_imports`, and optional `max_layers_touched` per layer, and set `test_command`.
   - **Root & Scoped `AGENTS.md`** (<200 lines each): Document system overview, schema/API invariants, and `Always / Ask First / Never` action tiers. Keep detailed runbooks in `.agents/skills/<role>/SKILL.md`.
   - **`docs/adr/NNN-slug.md`**: Add machine-readable ADRs with YAML frontmatter (`id`, `title`, `status`, `applies_to`, `summary`) for non-obvious constraints so `sync_context.py` routes each ADR into matching scoped `AGENTS.md` files.
   - **Domain Invariants & Golden Tests**: Add `validate_invariants()` checks on core models and fixture regression tests under `tests/`.

3. **Verify Closed-Loop Sync:**
   ```bash
   python3 scripts/sync_context.py && python3 scripts/verify_architecture.py
   ```
   Confirm all `<!-- AUTO-GENERATED -->` blocks are populated and all v3.0 checks pass.
