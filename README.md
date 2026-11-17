# AI-Native Repo Context & Guardrails Engine (`ai-native-repo`)

Turn any repository into a **self-updating, guardrail-enforced codebase** built for autonomous AI software engineers (**Antigravity**, **Claude Code**, **Cursor**, **Codex**, and **Gemini CLI**).

Instead of relying on static `README` files that rot after the first edit, `ai-native-repo` provides:
1. **Real-Time AST Contract Sync (`sync_context.py`):** Automatically keeps `<!-- AUTO-GENERATED:START --> ... <!-- AUTO-GENERATED:END -->` sections in root and directory-scoped `AGENTS.md` files synced with live Python/TypeScript signatures, dataclasses, constants, and internal imports.
2. **Declarative Layer Boundary Linter (`.ai-native.json` + `verify_architecture.py`):** Mechanically prevents agents from crossing layer boundaries, violating `AGENTS.md` token/line budgets (`<= 200` lines), or leaving bare `except: pass` smells.
3. **Machine-Readable ADR Routing (`docs/adr/*.md`):** Parses YAML frontmatter (`applies_to`, `id`, `summary`) and automatically injects governing ADRs into matching subdirectory `AGENTS.md` files.
4. **Closed-Loop Lifecycle Hooks (`hooks.json`):**
   - `PostToolUse`: Re-syncs `AGENTS.md` in `<50ms` after every file edit.
   - `Stop`: Blocks an agent from finishing if tests/boundaries fail or if public contracts changed without updating semantic notes (**Semantic Drift Gate**).
5. **1-Command Repo Bootstrapper (`bootstrap_repo.py`) & Git Pre-Commit Hook:** Zero external dependencies (Python 3 stdlib only).

---

## Installation

### Option A: For AI Coding Agents (Copy-Paste Prompt)
Open any repository in **Antigravity**, **Claude Code**, or **Cursor** and paste:
> *"Install the `ai-native-repo` plugin from `https://github.com/njindal96/ai-native-repo` (using `install.sh`) and bootstrap this repository with AI-native `AGENTS.md` contracts, `.ai-native.json` boundaries, ADRs, and pre-commit hooks."*

### Option B: For Humans (1-Line Terminal Install)
**Install the global plugin (for Antigravity):**
```bash
curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash
```

**Install the global plugin AND bootstrap an existing repo in one command:**
```bash
curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash -s -- --init /path/to/your-repo
```

Or clone manually:
```bash
git clone https://github.com/njindal96/ai-native-repo ~/.gemini/config/plugins/ai-native-repo
python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace /path/to/your-repo
```

---

## How It Works Automatically for Every New Agent

| Lifecycle Moment | What Runs | Result |
| :--- | :--- | :--- |
| **1. Agent arrives** | `rules/AGENTS.md` + root `AGENTS.md` (`CLAUDE.md` symlink) | Agent receives global architecture map, `Always / Ask First / Never` tiers, and `docs/adr/` index. |
| **2. Agent opens a module** | Subdirectory `AGENTS.md` (e.g., `pipeline/courts/AGENTS.md`) | Agent only loads contracts, internal imports, and ADRs relevant to that layer. |
| **3. Agent edits code** | `PostToolUse` hook -> `sync_context.py` | Updated function/class/dataclass signatures are immediately written into `AGENTS.md`. |
| **4. Agent tries to finish** | `Stop` hook -> `verify_architecture.py` + Semantic Drift Gate | Blocks exit if layer boundaries, syntax, tests, or context budgets fail—or if contracts changed without semantic doc updates. |
| **5. Terminal git commit** | `.githooks/pre-commit` | Guarantees every commit (even without an AI runtime) stays synced and verified. |

---

## Using with Claude Code / Other Agents
- **`CLAUDE.md` Symlink:** `bootstrap_repo.py` automatically creates `CLAUDE.md -> AGENTS.md` so Claude Code, Cursor, and Antigravity share one source of truth.
- **Claude Code Hooks:** Both `scripts/post_tool_hook.py` and `scripts/stop_gate_hook.py` accept Claude Code's `PostToolUse` / `Stop` stdin payloads natively (`stop_hook_active` / `{"decision": "block"}`), so you can wire them directly in `~/.claude/settings.json` if desired.

## License
MIT License — see [LICENSE](LICENSE).
