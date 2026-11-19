# `ai-native-repo` — Real-Time Context & Zero-Collateral Verification Engine (v3.3)

Turn any repository into a **self-updating, context-first, friction-aware, guardrail-enforced codebase** built for autonomous AI software engineers (**Antigravity**, **Claude Code**, **Cursor**, **Codex**, and **Gemini CLI**).

**Zero external dependencies** (pure Python 3 stdlib). **Single-hop Context Capsules (`--for`).** **Zero silent agent regressions.**

---

## Why `ai-native-repo`? (Benefits & Problem Solved)

Human docs and traditional unit tests break down when AI agents write code autonomously:
1. **Agents miss the "Why":** Without knowing *why* code was built a certain way and *what alternatives were rejected*, agents propose harmful "improvements" that re-introduce domain bugs.
2. **Agents waste turns grepping:** Without reverse dependencies (`Used By`) and test mappings (`Tested By`), agents make 5–15 exploratory calls before editing one function.
3. **Course-corrections & repeated workflows vanish in chat:** When a human corrects an agent or runs the same multi-step workflow repeatedly, the lesson or automation opportunity disappears when the session ends.
4. **Tautological tests & reward-hacking:** Agents write weak tests, hallucinate symbols in unexecuted branches, weaken assertions, or run `git commit -n` / `--no-verify`.

### Before vs. After

| Dimension | Traditional Repo | With `ai-native-repo` v3.3 |
| :--- | :--- | :--- |
| **Agent Onboarding & Context Time** | New agent wastes 5–15 tool calls grepping files, callers, and tests. | **Single-hop Context Capsule (`python3 scripts/sync_context.py --for <file>`)** returns governing `AGENTS.md`, full ADR `Why`/`Do Not Do`, `Used By` callers, and `Tested By` test functions in one call. |
| **Enforcing the "Why" Before Editing** | Agent edits production files blind and breaks original domain assumptions. | **`PreToolUse` Pre-Edit Context Gate** tracks `AGENTS.md`/`docs/adr/*.md` reads and blocks blind edits once while injecting the Context Capsule inline; `check_adr_completeness()` enforces `Context / Why` & `Anti-Patterns` sections in all ADRs. |
| **Learning From Friction (0 Tokens)** | Human corrections, exploration thrashing in un-onboarded repos, and repeated shell sequences vanish. | **Silent Friction & Opportunity Detector (`PostToolUse` + `Stop`):** Passively counts reads/reworks/workflows in `<5ms` (0 LLM tokens), stays invisible on normal turns, and emits a 1-shot cooldown-guarded nudge only when 1 of 4 hard thresholds fires (`ADR` update, `bootstrap_repo`, `Skill`, or `Automation`). |
| **Doc Freshness & Reverse Graph** | Signatures, callers, and tests go stale immediately. | `PostToolUse` hook + `.githooks/pre-commit` re-sync `<!-- AUTO-GENERATED -->` contracts, `Used By` blast radius, and `Tested By` targets in `<50ms`. |
| **Hallucinations & Stubs** | Unexecuted branches hide hallucinated imports, wrong dataclass fields, or `NotImplementedError` stubs. | Cross-module Python AST resolver checks imports (including relative & `__init__.py` re-exports), module aliases, and inherited `@dataclass` fields (`init=False` / `KW_ONLY` aware). |
| **Security, Cycles & Custom Gates** | Agents introduce hardcoded tokens, `eval()`, SQL interpolation, missing HTTP timeouts, or circular imports. | **Zero-dependency SAST & Cycle Guard** catches secrets, unsafe deserialization/sinks, `verify=False`, missing `requests` timeouts, and circular imports (`# ai-native-ignore` opt-out); `.ai-native.json` supports pluggable `custom_gates` (`bandit`, `semgrep`, `ruff`, `npm audit`, `gosec`). |
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
│   ├── sync_context.py           # Portable Tier 1 AST + Tier 2 lexical introspector, --for Context Capsule & ADR router
│   └── verify_architecture.py    # Portable v3.2 verifier (boundaries, ADR Why check, hallucinations, isolated mutations)
└── .githooks/
    ├── pre-commit                # Chains pre-commit.local (if present), syncs AGENTS.md & runs verify_architecture.py
    └── pre-push                  # Chains pre-push.local (if present) & runs full verify_architecture.py
```
Plus a scoped `AGENTS.md` inside each auto-detected code directory (e.g., `app/domain/AGENTS.md`, `app/services/AGENTS.md`).

Fetch a single-hop Context Capsule for any file at any time:
```bash
python3 scripts/sync_context.py --for app/services/billing.py
```

---

### Step 4: Optional Customization (`.ai-native.json` & ADRs)

1. **Tighten Layer Boundaries, Blast Radius & Mutation Caps in `.ai-native.json`:**
   ```json
   {
     "version": "3.0",
     "context_budget": { "max_lines": 200, "max_bytes": 20000 },
     "test_command": "python3 -m unittest discover -s tests -p 'test_*.py'",
     "enforce_contract_compatibility": true,
     "enforce_pre_edit_context": true,
     "enforce_security_checks": true,
     "mutation_testing": true,
     "max_mutants": 25,
     "max_layers_touched": 3,
     "custom_gates": [
       { "name": "ruff", "command": "ruff check .", "timeout": 30 }
     ],
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

   ## Context & Why
   Explain why this architecture exists and what production bug or constraint shaped it.

   ## Decision & Invariants
   State what must hold true in the final state after any change.

   ## Anti-Patterns / Do Not Do
   Explicitly list rejected alternatives or dangerous shortcuts.
   ```
3. **Commit the Onboarded Files:**
   ```bash
   git add .ai-native.json AGENTS.md CLAUDE.md docs/adr/ scripts/ .githooks/ && git commit -m "Onboard repo to ai-native-repo v3.2"
   ```

## License
MIT License — see [LICENSE](LICENSE).
