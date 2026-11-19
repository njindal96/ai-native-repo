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
1. **Pillar 1 (`PreToolUse` Shell & Pre-Edit Context Gate):** `scripts/pre_tool_hook.py` hard-blocks `--no-verify` / `git commit -n` / `core.hooksPath` overrides, gates `git push` behind `verify_workspace(target, run_tests=True)`, and tracks reads of `AGENTS.md` / `docs/adr/*.md` to block blind edits to existing layer files while injecting a single-hop **Context Capsule** (Why/ADRs, `Used By` callers, `Tested By` targets).
2. **Pillar 2 (Anti-Hallucination, Security/Reliability SAST & Cycle Guard):** `verify_architecture.py` statically checks bare `except: pass`, unimplemented non-abstract `NotImplementedError` stubs, cross-module `from <internal> import <sym>` definitions, `@dataclass` keyword arguments, circular internal imports (`check_import_cycles()`), high-precision security/reliability risks (`check_security_and_reliability()`), and pluggable `"custom_gates"` (`run_custom_gates()`).
3. **Pillar 3 (Anti-Regression & Blast-Radius Guard):** Compares working tree AST against `git HEAD` to block deleted tests, newly skipped tests, reduced assertion counts, removed `@dataclass` fields, and layer blast-radius overruns (`max_layers_touched`).
4. **Pillar 4 (Domain Invariants & ADR Rationale Enforcement):** Target repositories embed explicit `validate_invariants()` methods, golden fixture assertions, and complete ADRs validated by `check_adr_completeness()` (requiring `Context / Why` and `Decision / Invariants / Anti-Patterns`).
5. **Pillar 5 (Diff-Scoped AST Micro-Mutation Testing):** `run_diff_mutation_check` mutates boolean/comparison nodes on lines touched in `git diff HEAD` inside an isolated `TemporaryDirectory` copy with hard per-mutant timeouts, flagging surviving mutants when tests fail to catch inverted logic.
6. **Pillar 6 (Semantic Drift Gate, Single-Hop Context & Self-Propagating Compatibility):** `sync_context.py` indexes Python & polyglot `Used By` and `Tested By` and supports `--for <file>` Context Capsules; `post_tool_hook.py` automatically propagates updated plugin scripts into onboarded repos (`auto_upgrade_repo_scripts()`) and runs the 4-rule Silent Friction & Opportunity Detector; `stop_gate_hook.py` blocks agent completion if a scoped `AGENTS.md` public contract changed without updating semantic notes.

## Anti-Patterns / Do Not Do
- Never add third-party PyPI packages inside `scripts/`; everything must remain runnable under `python3 -I`.
- Never hardcode repo-specific paths, commands, or domain assumptions inside the plugin or break backward compatibility with legacy `.ai-native.json` / ADR / agent hook schemas.
- Never allow agents to modify production layer code blind without delivering the governing `Context / Why` and `Do Not Do` invariants.
