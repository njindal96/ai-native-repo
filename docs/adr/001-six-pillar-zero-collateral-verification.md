---
id: ADR-001
title: Six-Pillar Zero-Collateral Verification Harness
status: accepted
applies_to: *
summary: Enforce PreToolUse hook-bypass blocking, cross-module AST symbol resolution, anti-test-tampering & contract compatibility checks vs HEAD, domain invariants, diff-scoped AST micro-mutation testing, and semantic drift gating using Python stdlib only.
---

# ADR-001: Six-Pillar Zero-Collateral Verification Harness

## Context
Traditional unit test runners do not stop autonomous coding agents from reward-hacking (weakening assertions, skipping failing tests, using `--no-verify`), hallucinating imports/fields in unexecuted branches, causing collateral contract regressions across layers, or writing tautological tests that pass even when core conditions are inverted.

## Decision & Invariants
1. **Pillar 1 (`PreToolUse` Gate):** `scripts/pre_tool_hook.py` hard-blocks `--no-verify` / `git commit -n` and gates `git push` behind `verify_workspace(target, run_tests=True)`.
2. **Pillar 2 (Anti-Hallucination & Hygiene):** `verify_architecture.py` statically checks bare `except: pass`, unimplemented non-abstract `NotImplementedError` stubs, hardcoded secret patterns, cross-module `from <internal> import <sym>` definitions, and `@dataclass` keyword arguments.
3. **Pillar 3 (Anti-Regression & Blast-Radius Guard):** Compares working tree AST against `git HEAD` to block deleted tests, newly skipped tests, reduced assertion counts, removed `@dataclass` fields, and layer blast-radius overruns (`max_layers_touched`).
4. **Pillar 4 (Domain Invariants & Golden Contracts):** Target repositories embed explicit `validate_invariants()` methods and golden fixture assertions.
5. **Pillar 5 (Diff-Scoped AST Micro-Mutation Testing):** `run_diff_mutation_check` mutates boolean/comparison nodes on lines touched in `git diff HEAD` (restoring the file in `finally:`) and flags surviving mutants when tests fail to catch inverted logic.
6. **Pillar 6 (Semantic Drift Gate):** `stop_gate_hook.py` blocks agent completion if a scoped `AGENTS.md` contract block changed without updating semantic notes.
