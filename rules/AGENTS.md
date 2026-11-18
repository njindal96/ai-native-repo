# AI-Native Repository Protocol (v3.0)

When operating as a software engineering agent in any repository with `AGENTS.md`:

1. **Progressive & Single-Hop Context Navigation:**
   - Start at root `AGENTS.md` (<200 lines) for the global map, commands, and layer table, or run `python3 scripts/sync_context.py --for <target_file>` to fetch the full Context Capsule (Why/ADRs, `Used By` callers, `Tested By` tests, and live contract) in one call.
   - Before editing any directory, read its local `AGENTS.md` and any linked `docs/adr/*.md` files (especially `Context & Why`, `Decision & Invariants`, and `Anti-Patterns / Do Not Do`) whose `applies_to` matches your target files.
2. **Respect Explicit Action Tiers (`Always` / `Ask First` / `Never`):**
   - Never violate `Never` rules or cross forbidden layer import boundaries declared in `.ai-native.json`.
   - Never weaken or delete existing tests, skip assertions, drop public `@dataclass` fields, or pass `--no-verify` to bypass Git hooks.
   - Never hand-edit inside `<!-- AUTO-GENERATED:START --> ... <!-- AUTO-GENERATED:END -->` blocks.
3. **Closed-Loop Verification & Semantic Sync:**
   - Run `python3 scripts/verify_architecture.py` after edits to validate syntax, cross-module symbol resolution, layer boundaries, contract compatibility, unit tests, and diff-scoped mutation coverage.
   - Whenever your changes alter the auto-generated public contract (adding/removing/changing classes, dataclasses, functions, or modules), you MUST also update the semantic section of the corresponding `AGENTS.md` (and add/update a `docs/adr/*.md` if an architectural invariant changed) before finishing your turn.
4. **Universal Multi-Repo Scalability & Compatibility (When Editing Plugin / Shared Infrastructure):**
   - Any enhancement to shared AI-native tooling/hooks must be repository- and stack-agnostic, non-destructively upgrade previously onboarded repositories, and preserve backward compatibility with older `.ai-native.json`, ADR schemas, and all 5 agent hook payload formats.
