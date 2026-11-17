# `ai-native-repo` — Real-Time Context & Guardrails Engine for AI Coding Agents

Turn any repository into a **self-updating, boundary-enforced codebase** built for autonomous AI software engineers (**Antigravity**, **Claude Code**, **Cursor**, **Codex**, and **Gemini CLI**).

**Zero external dependencies** (pure Python 3 stdlib). **Zero manual doc maintenance.**

---

## Why `ai-native-repo`? (Benefits & Problem Solved)

Human documentation (`README.md`, wikis, Google Docs) breaks down immediately when AI agents write code: it goes stale on the very first edit, overwhelms context windows if dumped in one giant file, and cannot stop a fresh subagent from accidentally coupling layers or breaking hidden database/API invariants.

### Before vs. After

| Dimension | Traditional Repo | With `ai-native-repo` |
| :--- | :--- | :--- |
| **Agent Onboarding** | New agent wastes 5–15 tool calls grepping files to figure out architecture & conventions. | Agent reads compact root `AGENTS.md` (<200 lines) + scoped layer `AGENTS.md` on arrival and starts in the right place immediately. |
| **Doc Freshness** | Function signatures, dataclasses, and module maps go stale as soon as code changes. | `PostToolUse` hook + `.githooks/pre-commit` re-sync `<!-- AUTO-GENERATED -->` contracts from live AST in `<50ms` after every edit. |
| **Layer Isolation** | Agents import DB clients into domain/UI code because "it works locally." | `.ai-native.json` + `verify_architecture.py` mechanically fail any forbidden cross-layer import before the agent can finish. |
| **Architectural Memory** | Future agents undo non-obvious fixes (e.g., CSRF stripping, SQL trigger patterns). | Machine-readable ADRs (`docs/adr/*.md` with `applies_to`) are automatically routed into matching directory `AGENTS.md` files. |
| **Agent Handoffs** | Agent A changes behavior and leaves silently; Agent B hallucinates against old prose. | `Stop` hook (**Semantic Drift Gate**) blocks an agent from finishing if public contracts changed without updating semantic notes. |

### 5 Core Benefits
1. **Fresh Agents Are Instantly Productive:** A narrow-role agent only loads the rules, live signatures, and ADRs for the directory it touches—zero token bloat or "context rot."
2. **Living Documentation That Never Lies:** Class fields, function signatures, constants, internal import graphs, and TypeScript exports stay 100% synced with source code automatically.
3. **Mechanical Guardrails Over Polite Suggestions:** Prose rules alone get forgotten; `.ai-native.json` + `verify_architecture.py` enforce import boundaries, bare `except: pass` checks, context budgets (`<= 200` lines), and unit tests deterministically.
4. **Works Across Every AI Tool & CI:** One `AGENTS.md` hierarchy + `CLAUDE.md` symlink + cross-agent `PostToolUse`/`Stop` hooks + portable `.githooks/pre-commit` + GitHub Actions CI.
5. **1-Command Onboarding for Any Repo:** Works out of the box on Python and TypeScript/JS repositories with zero PyPI/npm packages required.

---

## How to Onboard Any Repository

### Step 1: One-Time Global Plugin Install (On Your Machine)

**Option A — Ask Your AI Agent (Antigravity / Claude Code / Cursor):**
Paste this prompt into any chat:
> *"Install `ai-native-repo` from `https://github.com/njindal96/ai-native-repo` using `install.sh`."*

**Option B — 1-Line Terminal Install (Humans):**
```bash
curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash
```
*(Clones to `~/.gemini/config/plugins/ai-native-repo` and makes all scripts executable.)*

---

### Step 2: Bootstrap Any New or Existing Repository

Choose whichever method fits your workflow:

1. **Via AI Agent (Recommended — Zero Terminal Work):**
   Open the target repository in Antigravity (or any agent) and ask:
   > *"Initialize AI-native setup in this repo."*
   *(In Antigravity, the installed `init-ai-native-repo` skill runs `bootstrap_repo.py` and helps tailor layer rules and ADRs to your codebase.)*

2. **Via 1-Line Terminal Command (Install + Bootstrap Together):**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash -s -- --init /path/to/your-repo
   ```

3. **Via Direct Python Command (Already Installed):**
   ```bash
   python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace /path/to/your-repo
   ```

---

### Step 3: What Gets Created in Your Repository

`bootstrap_repo.py` is **non-destructive** (it never overwrites existing files) and sets up:

```text
your-repo/
├── .ai-native.json               # Declarative layer import rules, context budgets & test_command
├── AGENTS.md                     # Lean (<200 lines) global map, action tiers & auto-synced index
├── CLAUDE.md -> AGENTS.md        # Symlink so Claude Code & Cursor read the exact same context
├── docs/adr/
│   └── 001-ai-native-architecture.md  # Starter machine-readable ADR (YAML frontmatter routed)
├── scripts/
│   ├── sync_context.py           # Portable AST + ADR introspector (runs in CI & clones)
│   └── verify_architecture.py    # Portable boundary, smell, budget & unit-test verifier
└── .githooks/
    └── pre-commit                # Auto-syncs & verifies on every `git commit`
```
Plus a scoped `AGENTS.md` inside each auto-detected code directory (e.g., `app/domain/AGENTS.md`, `app/services/AGENTS.md`).

---

### Step 4: Optional 2-Minute Customization (High Leverage)

1. **Lock Down Layer Boundaries in `.ai-native.json`:**
   ```json
   {
     "version": "2.0",
     "context_budget": { "max_lines": 200, "max_bytes": 20000 },
     "test_command": "python3 -m unittest discover -s tests -p 'test_*.py'",
     "layers": [
       {
         "path": "app/domain",
         "allowed_internal_prefixes": ["app.domain"],
         "forbidden_imports": ["requests", "sqlalchemy", "app.services"],
         "reason": "Domain layer must remain pure business logic."
       }
     ]
   }
   ```
2. **Capture Non-Obvious Decisions as Machine-Readable ADRs (`docs/adr/002-my-rule.md`):**
   ```markdown
   ---
   id: ADR-002
   title: Never Bypass Payment Idempotency Keys
   status: accepted
   applies_to: app/services/*
   summary: All billing endpoints must pass an idempotency_key header; never retry blindly.
   ---
   ```
   `sync_context.py` automatically surfaces `ADR-002` inside `app/services/AGENTS.md` and root `AGENTS.md`.
3. **Commit the Onboarded Files:**
   ```bash
   git add .ai-native.json AGENTS.md CLAUDE.md docs/adr/ scripts/ .githooks/ && git commit -m "Onboard repo to ai-native-repo v2.0"
   ```

---

## How It Works Day-to-Day (100% Automatic)

Once onboarded, **no special prompt or command is ever needed** when a new agent starts work:

| Lifecycle Event | What Runs Automatically | Guarantee |
| :--- | :--- | :--- |
| **1. Agent arrives** | `rules/AGENTS.md` + root `AGENTS.md` (`CLAUDE.md`) | Agent immediately sees system map, `Always / Ask First / Never` tiers, and ADR index. |
| **2. Agent enters a folder** | Nearest subdirectory `AGENTS.md` | Agent receives live signatures, internal deps, and governing ADRs for that directory only. |
| **3. Agent edits a file** | `PostToolUse` hook -> `sync_context.py` | Updated classes, dataclasses, functions, constants, and imports appear in `AGENTS.md` immediately. |
| **4. Agent finishes its turn** | `Stop` hook -> `verify_architecture.py` + **Semantic Drift Gate** | Blocks completion if syntax, layer boundaries, budgets, or tests fail—or if contracts changed without updating semantic `AGENTS.md` notes. |
| **5. Terminal commit / CI** | `.githooks/pre-commit` & CI workflow | Guarantees `main` never accepts stale `AGENTS.md` files or broken boundaries. |

---

## Multi-Tool Compatibility (Antigravity, Claude Code, Cursor, Codex, CI)
- **Antigravity:** Discovers `plugin.json`, `rules/AGENTS.md`, `hooks.json`, and `skills/init-ai-native-repo/` automatically on next turn once cloned to `~/.gemini/config/plugins/ai-native-repo`.
- **Claude Code:** Reads `CLAUDE.md -> AGENTS.md` natively, and `scripts/post_tool_hook.py` / `scripts/stop_gate_hook.py` accept Claude Code's `PostToolUse` / `Stop` JSON payloads (`stop_hook_active`, `{"decision": "block"}`).
- **Cursor / Codex / Windsurf / CI:** Reads `AGENTS.md` / `SKILL.md` directly and executes `scripts/sync_context.py` & `scripts/verify_architecture.py` via `.githooks/pre-commit` or GitHub Actions.

## License
MIT License — see [LICENSE](LICENSE).
