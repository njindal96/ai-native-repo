# `ai-native-repo` — Real-Time Context & Zero-Collateral Verification Engine (v3.1)

Turn any repository into a **self-updating, guardrail-enforced codebase** built for autonomous AI software engineers (**Antigravity**, **Claude Code**, **Cursor**, **Codex**, and **Gemini CLI**).

**Zero external dependencies** (pure Python 3 stdlib). **Zero manual doc maintenance.** **Zero silent agent regressions.**

---

## Why `ai-native-repo`? (Benefits & Problem Solved)

Human docs and traditional unit tests break down when AI agents write code autonomously:
1. Documentation rots on the first edit, or floods context windows if kept in one giant file.
2. Agents share blind spots with the tests they write (the **"Tautology Trap"**), hallucinate symbols in unexecuted branches, or cause collateral damage across layers.
3. Under outcome pressure (*"make tests pass"*), agents can **reward-hack** by weakening assertions, skipping failing tests, or running `git commit --no-verify` / `git commit -amn` / `git -c core.hooksPath=/dev/null`.

### Before vs. After

| Dimension | Traditional Repo | With `ai-native-repo` v3.1 |
| :--- | :--- | :--- |
| **Agent Onboarding** | New agent wastes 5–15 tool calls grepping files to understand layout & rules. | Agent reads lean root `AGENTS.md` (<200 lines) + scoped layer `AGENTS.md` (existing `CLAUDE.md` merged cleanly). |
| **Doc Freshness** | Function signatures, dataclasses, and module maps go stale immediately. | `PostToolUse` hook + `.githooks/pre-commit` re-sync `<!-- AUTO-GENERATED -->` contracts cleanly in `<50ms`. |
| **Hallucinations & Stubs** | Unexecuted branches hide hallucinated imports, wrong dataclass fields, or `NotImplementedError` stubs. | Cross-module Python AST resolver checks imports (including relative & `__init__.py` re-exports), module aliases, and inherited `@dataclass` fields (`init=False` / `KW_ONLY` aware). |
| **Collateral Damage & Test Tampering** | Agent deletes tests, weakens assertions, drops model fields, edits `.ai-native.json`, or bypasses hooks with `-n`. | `PreToolUse` tokenizes shell commands to block `-n`/`--no-verify`/`core.hooksPath` overrides & `chmod -x`; `verify_architecture.py` checks tests, dataclasses, `.ai-native.json`, and `.githooks/` vs `HEAD`. |
| **Weak / Tautological Tests** | Agent writes trivial tests that pass even if logic is broken. | **Isolated tempdir diff-scoped AST micro-mutation testing** tests changed Python branches with per-mutant timeout (zero working-tree corruption). |
| **Architectural Memory & Handoffs** | Next agent undoes hidden invariants or leaves without documenting changes. | Machine-readable ADRs (`docs/adr/*.md`) route into matching `AGENTS.md` files, and `Stop` gate blocks semantic drift (whitespace-bypass resistant). |

---

## Language Support Tiers (Pure Python 3 Stdlib)

Because `ai-native-repo` strictly guarantees **zero third-party PyPI dependencies**, deep compiler AST checks focus on Python stdlib `ast`, while non-Python languages use a comment- and string-literal-stripped lexical engine:

| Tier | Languages | Capabilities Supported |
| :--- | :--- | :--- |
| **Tier 1 (Full AST)** | **Python (`.py`)** | Full contract sync (`classes`, inherited `@dataclass` fields, `init=False`, `async`/`sync` signatures, constants), syntax check, bare `except: pass` & stub detection, relative & `__init__.py` cross-module symbol/alias resolution, layer import boundaries, contract regression checks vs `HEAD`, and isolated tempdir AST micro-mutation testing. |
| **Tier 2 (Lexical)** | **TypeScript / JavaScript** (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`), **Go** (`.go`), **Rust** (`.rs`), **Java** (`.java`) | Comment- and string-stripped multiline export extraction into `AGENTS.md`, plus comment-stripped `forbidden_imports` layer boundary linting and `max_layers_touched` blast-radius checks. *(No non-Python AST mutation testing or cross-file type resolution.)* |

---

## Supported Agent Integration Matrix

| Agent | Shared Context Discovery | Real-Time Tool & Stop Hooks | Rules / Skills | Universal Offline Enforcement |
| :--- | :--- | :--- | :--- | :--- |
| **Antigravity** | `AGENTS.md` (root + scoped) | Native (`hooks.json`: `PreToolUse`, `PostToolUse`, `Stop`) | `rules/AGENTS.md` + `skills/init-ai-native-repo` | `.githooks/pre-commit` & `.githooks/pre-push` (chaining `.local`) |
| **Claude Code** | `CLAUDE.md -> AGENTS.md` (merged non-destructively) | Compatible (`PreToolUse`, `PostToolUse`, `Stop` via `--merge-settings`) | Reads `AGENTS.md` / `CLAUDE.md` directly | `.githooks/pre-commit` & `.githooks/pre-push` |
| **Gemini CLI** | `AGENTS.md` + `CLAUDE.md` | Compatible defensive stdin/stdout schema | Reads `AGENTS.md` + CLI system context | `.githooks/pre-commit` & `.githooks/pre-push` |
| **Cursor** | `AGENTS.md` + `CLAUDE.md` | Compatible defensive hook scripts if configured | `.cursorrules` / `AGENTS.md` | `.githooks/pre-commit` & `.githooks/pre-push` |
| **Codex** | `AGENTS.md` (native standard) | N/A (executes inside sandboxed shell) | `AGENTS.md` + `Always`/`Ask First`/`Never` | `.githooks/pre-commit` & `.githooks/pre-push` |

---

## How to Onboard Any Repository

### Step 1: One-Time Global Plugin Install

**Option A — Ask Your AI Agent (Antigravity / Claude Code / Cursor):**
> *"Install `ai-native-repo` from `https://github.com/njindal96/ai-native-repo` using `install.sh`."*

**Option B — 1-Line Terminal Install (Humans):**
```bash
curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash
```

Optional non-destructive hook merge into an existing agent JSON config (preserves all existing hooks & keys):
```bash
bash ~/.gemini/config/plugins/ai-native-repo/install.sh --merge-settings ~/.claude/settings.json
```

---

### Step 2: Bootstrap Any New or Existing Repository

1. **Via AI Agent (Recommended):**
   Open your target repo and ask:
   > *"Initialize AI-native setup in this repo."*
2. **Via 1-Line Terminal Command (Install + Bootstrap Together):**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/njindal96/ai-native-repo/main/install.sh | bash -s -- --init /path/to/your-repo
   ```
3. **Via Direct Python Command (Already Installed):**
   ```bash
   python3 ~/.gemini/config/plugins/ai-native-repo/scripts/bootstrap_repo.py --workspace /path/to/your-repo
   ```

---

### Step 3: What Gets Created (Non-Destructively)

`bootstrap_repo.py` preserves existing `.git/hooks/*` or custom `core.hooksPath` hooks (backing them up & chaining them from `.githooks/*.local`), merges any existing `CLAUDE.md` prose into `AGENTS.md` before symlinking (with copy fallback on Windows), and scaffolds:

```text
your-repo/
├── .ai-native.json               # Layer rules, context budgets, contract compatibility & mutation gates
├── AGENTS.md                     # Lean (<200 lines) global map, Always/Ask First/Never tiers & live index
├── CLAUDE.md -> AGENTS.md        # Symlink (or copy fallback on Windows) for Claude Code & Cursor
├── docs/adr/
│   └── 001-ai-native-architecture.md  # Starter machine-readable ADR (YAML frontmatter routed)
├── scripts/
│   ├── sync_context.py           # Portable Tier 1 AST + Tier 2 lexical introspector & ADR router
│   └── verify_architecture.py    # Portable v3.1 verifier (boundaries, hallucinations, regressions, isolated mutations)
└── .githooks/
    ├── pre-commit                # Chains pre-commit.local (if present), syncs AGENTS.md & runs verify_architecture.py
    └── pre-push                  # Chains pre-push.local (if present) & runs full verify_architecture.py
```
Plus a scoped `AGENTS.md` inside each auto-detected code directory (e.g., `app/domain/AGENTS.md`, `app/services/AGENTS.md`).

---

### Step 4: Optional Customization (`.ai-native.json` & ADRs)

1. **Tighten Layer Boundaries, Blast Radius & Mutation Caps in `.ai-native.json`:**
   ```json
   {
     "version": "3.0",
     "context_budget": { "max_lines": 200, "max_bytes": 20000 },
     "test_command": "python3 -m unittest discover -s tests -p 'test_*.py'",
     "enforce_contract_compatibility": true,
     "mutation_testing": true,
     "max_mutants": 25,
     "max_layers_touched": 3,
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
2. **Add Machine-Readable ADRs (`docs/adr/002-my-invariant.md`):**
   ```markdown
   ---
   id: ADR-002
   title: "ADR-02: Never Bypass Payment Idempotency Keys"
   status: accepted
   applies_to:
     - app/services/*
     - app/api/*
   summary: All billing endpoints must pass an idempotency_key header; never retry blindly.
   ---
   ```
3. **Commit the Onboarded Files:**
   ```bash
   git add .ai-native.json AGENTS.md CLAUDE.md docs/adr/ scripts/ .githooks/ && git commit -m "Onboard repo to ai-native-repo v3.1"
   ```

## License
MIT License — see [LICENSE](LICENSE).
