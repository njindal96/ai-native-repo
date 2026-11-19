---
name: init-ai-native-repo
description: Bootstraps or updates a repository with AI-Native v3.3 best practices: hierarchical AGENTS.md (<200 lines) with Used By & Tested By reverse maps, single-hop Context Capsules (--for), .ai-native.json pre-edit context/import/contract/mutation guardrails, rationale-enforced ADRs (docs/adr/), and the Silent Friction & Opportunity Detector.
---

# Initialize AI-Native Repository (v3.3)

Use this skill when bootstrapping or upgrading any Python or Polyglot (TS/JS/Go/Rust/Java) repository designed for autonomous AI coding agents.

## Workflow

1. **Run the Automated 1-Command Bootstrapper:**
   ```bash
   python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace .
   ```
   This automatically creates or non-destructively upgrades:
   - `scripts/sync_context.py` (`Used By`, `Tested By`, `--for <file>` Context Capsules) and `scripts/verify_architecture.py` (`check_adr_completeness()`, AST boundaries, `check_security_and_reliability()`, `check_import_cycles()`, `custom_gates`, isolated diff-scoped mutations)
   - `.ai-native.json` (with `enforce_contract_compatibility`, `enforce_pre_edit_context`, `enforce_security_checks`, `mutation_testing`, `max_mutants`, `context_budget`, `test_command`, and auto-detected `layers`)
   - Root `AGENTS.md` + `CLAUDE.md` symlink + scoped `*/AGENTS.md` files with `<!-- AUTO-GENERATED -->` markers
   - Starter `docs/adr/001-ai-native-architecture.md` (with required `Context & Why`, `Decision & Invariants`, and `Anti-Patterns / Do Not Do` sections)
   - `.githooks/pre-commit` + `.githooks/pre-push` (`git config core.hooksPath .githooks`, chaining `.local` hooks)

2. **Customize Domain Boundaries, Invariants & ADRs:**
   - **`.ai-native.json`**: Tighten `allowed_internal_prefixes`, `forbidden_imports`, optional `max_layers_touched`, and optional `poll_command_prefixes`, and set `test_command`.
   - **Root & Scoped `AGENTS.md`** (<200 lines each): Document system overview, schema/API invariants, and `Always / Ask First / Never` action tiers. Fetch any file's single-hop capsule anytime via `python3 scripts/sync_context.py --for <target_file>`.
   - **`docs/adr/NNN-slug.md`**: Add machine-readable ADRs with YAML frontmatter (`id`, `title`, `status`, `applies_to`, `summary`) plus explicit `## Context & Why`, `## Decision & Invariants`, and `## Anti-Patterns / Do Not Do` headings.
   - **Domain Invariants & Golden Tests**: Add `validate_invariants()` checks on core models and fixture regression tests under `tests/`.

3. **Verify Closed-Loop Sync:**
   ```bash
   python3 scripts/sync_context.py && python3 scripts/verify_architecture.py
   ```
   Confirm all `<!-- AUTO-GENERATED -->` blocks are populated (`Used By` / `Tested By` included) and all v3.3 checks pass. Global plugin hooks automatically keep onboarded repo scripts up to date and surface silent friction/opportunity nudges when course-corrections or repeated workflows occur.
