import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import post_tool_hook  # noqa: E402
import pre_tool_hook  # noqa: E402
import stop_gate_hook  # noqa: E402
from bootstrap_repo import bootstrap_repository, merge_hooks_into_settings  # noqa: E402
from pre_tool_hook import extract_command, validate_shell_command  # noqa: E402
from stop_gate_hook import check_semantic_drift, split_semantic_and_auto  # noqa: E402
from sync_context import (  # noqa: E402
    END_MARKER,
    START_MARKER,
    adr_matches_directory,
    build_context_capsule,
    build_reverse_index,
    inspect_polyglot_file,
    parse_adr_frontmatter,
    sync_workspace,
)
from verify_architecture import (  # noqa: E402
    check_adr_completeness,
    check_git_head_regressions,
    check_symbol_and_dataclass_resolution,
    run_diff_mutation_check,
    verify_workspace,
)


def _init_git_repo(root: Path):
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(root), check=True, capture_output=True)


def _run_hook_inproc(mod, payload_obj) -> dict:
    raw = json.dumps(payload_obj) if not isinstance(payload_obj, str) else payload_obj
    buf_out = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(raw)), mock.patch("sys.stdout", buf_out):
        mod.main()
    return json.loads(buf_out.getvalue())


def _create_sample_app(root: Path):
    domain_dir = root / "app" / "domain"
    services_dir = root / "app" / "services"
    tests_dir = root / "tests"
    domain_dir.mkdir(parents=True, exist_ok=True)
    services_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (domain_dir / "__init__.py").write_text("", encoding="utf-8")
    (services_dir / "__init__.py").write_text("", encoding="utf-8")

    (domain_dir / "models.py").write_text(
        "from dataclasses import dataclass\n\n"
        "MAX_RETRY = 3\n\n"
        "@dataclass\n"
        "class Invoice:\n"
        "    amount: float\n",
        encoding="utf-8",
    )
    (services_dir / "billing.py").write_text(
        "from app.domain.models import Invoice\n\n"
        "def charge(inv: Invoice) -> bool:\n"
        "    return inv.amount > 0\n",
        encoding="utf-8",
    )
    (tests_dir / "test_billing.py").write_text(
        "import unittest\n"
        "from app.domain.models import Invoice\n"
        "from app.services.billing import charge\n\n"
        "class TestBilling(unittest.TestCase):\n"
        "    def test_charge(self):\n"
        "        self.assertTrue(charge(Invoice(amount=10.0)))\n"
        "        self.assertFalse(charge(Invoice(amount=0.0)))\n",
        encoding="utf-8",
    )


class TestAiNativeEngine(unittest.TestCase):
    def test_bootstrap_and_ast_sync_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)

            actions = bootstrap_repository(tmp_path)
            self.assertTrue(actions)

            for expected in (
                ".ai-native.json",
                "AGENTS.md",
                "CLAUDE.md",
                "app/domain/AGENTS.md",
                "app/services/AGENTS.md",
                "docs/adr/001-ai-native-architecture.md",
                "scripts/sync_context.py",
                "scripts/verify_architecture.py",
                ".githooks/pre-commit",
                ".githooks/pre-push",
            ):
                self.assertTrue((tmp_path / expected).exists(), f"Missing {expected}")

            self.assertTrue((tmp_path / "CLAUDE.md").is_symlink())
            domain_agents = (tmp_path / "app" / "domain" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("Dataclass `Invoice`", domain_agents)
            self.assertIn("MAX_RETRY", domain_agents)

            services_agents = (tmp_path / "app" / "services" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("app.domain.models", services_agents)
            self.assertIn("charge(inv: Invoice) -> bool", services_agents)

            self.assertEqual(verify_workspace(tmp_path, run_tests=True), [])

    def test_verify_architecture_catches_boundary_smell_and_budget_violations(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)

            cfg = {
                "version": "3.0",
                "context_budget": {"max_lines": 200, "max_bytes": 20000},
                "test_command": "",
                "layers": [
                    {
                        "path": "app/domain",
                        "allowed_internal_prefixes": ["app.domain"],
                        "forbidden_imports": ["requests", "app.services"],
                        "reason": "Domain must stay isolated",
                    }
                ],
            }
            (tmp_path / ".ai-native.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

            bad_file = tmp_path / "app" / "domain" / "bad.py"
            bad_file.write_text(
                "import requests\n"
                "from app.services import billing\n\n"
                "def stub_fn():\n"
                "    raise NotImplementedError()\n\n"
                "try:\n"
                "    pass\n"
                "except:\n"
                "    pass\n",
                encoding="utf-8",
            )
            (tmp_path / "app" / "domain" / "AGENTS.md").write_text(
                "\n".join(f"line {i}" for i in range(205)) + "\n",
                encoding="utf-8",
            )

            errs = verify_workspace(tmp_path, run_tests=False)
            joined = "\n".join(errs)
            self.assertIn("[LLM Smell]", joined)
            self.assertIn("[Unimplemented Stub]", joined)

            bad_file.write_text(
                "import requests\n"
                "from app.services import billing\n",
                encoding="utf-8",
            )
            errs2 = verify_workspace(tmp_path, run_tests=False)
            joined2 = "\n".join(errs2)
            self.assertIn("[Boundary Violation]", joined2)
            self.assertIn("requests", joined2)
            self.assertIn("app.services", joined2)
            self.assertIn("[Context Budget Exceeded]", joined2)

    def test_inline_backtick_markers_are_ignored_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rules_dir = tmp_path / "rules"
            rules_dir.mkdir(parents=True)

            prose_with_inline = (
                "# Protocol\n\n"
                "- Never edit inside `<!-- AUTO-GENERATED:START --> ... <!-- AUTO-GENERATED:END -->` blocks.\n"
            )
            (rules_dir / "AGENTS.md").write_text(prose_with_inline, encoding="utf-8")
            (tmp_path / "AGENTS.md").write_text(
                f"# Root\n\n- Inline mention: `{START_MARKER}` / `{END_MARKER}`.\n\n{START_MARKER}\n{END_MARKER}\n",
                encoding="utf-8",
            )

            changed_first = sync_workspace(tmp_path)
            self.assertEqual(changed_first, ["AGENTS.md"])
            self.assertEqual((rules_dir / "AGENTS.md").read_text(encoding="utf-8"), prose_with_inline)

            changed_second = sync_workspace(tmp_path)
            self.assertEqual(changed_second, [])

            sem, auto = split_semantic_and_auto((tmp_path / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("Inline mention", sem)
            self.assertIn("Scoped `AGENTS.md` Hierarchy", auto)

    def test_lifecycle_hooks_and_semantic_drift_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)
            cfg_p = tmp_path / ".ai-native.json"
            cfg_obj = json.loads(cfg_p.read_text(encoding="utf-8"))
            cfg_obj["mutation_testing"] = False
            cfg_p.write_text(json.dumps(cfg_obj), encoding="utf-8")

            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=str(tmp_path), check=True, capture_output=True)

            self.assertEqual(_run_hook_inproc(post_tool_hook, {"cwd": str(tmp_path)}), {})

            billing_path = tmp_path / "app" / "services" / "billing.py"
            billing_path.write_text(
                billing_path.read_text(encoding="utf-8")
                + "\ndef refund(inv_id: str) -> bool:\n    return len(inv_id) > 0\n",
                encoding="utf-8",
            )
            test_path = tmp_path / "tests" / "test_billing.py"
            test_path.write_text(
                test_path.read_text(encoding="utf-8")
                + "    def test_refund(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            agy_res = _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"})
            self.assertEqual(agy_res["decision"], "continue")
            self.assertIn("app/services/AGENTS.md", agy_res["reason"])

            cc_res = _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "stop_hook_active": False})
            self.assertEqual(cc_res["decision"], "block")
            self.assertIn("app/services/AGENTS.md", cc_res["reason"])

            svc_agents = tmp_path / "app" / "services" / "AGENTS.md"
            svc_text = svc_agents.read_text(encoding="utf-8").replace(
                "## Purpose & Invariants\n",
                "## Purpose & Invariants\n- Supports refunds via `refund()`.\n",
            )
            svc_agents.write_text(svc_text, encoding="utf-8")

            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "stop_hook_active": False}),
                {},
            )

    def test_pre_tool_hook_blocks_bypass_and_unverified_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)

            agy_res = _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "toolCall": {"name": "run_command", "args": {"CommandLine": "git commit --no-verify -m x"}},
            })
            self.assertEqual(agy_res["decision"], "deny")

            cc_res = _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -n -m x"},
            })
            self.assertEqual(cc_res["decision"], "block")

            (tmp_path / "app" / "services" / "billing.py").write_text("def broken(:\n", encoding="utf-8")
            push_res = _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "toolCall": {"name": "run_command", "args": {"CommandLine": "git push origin main"}},
            })
            self.assertEqual(push_res["decision"], "deny")
            self.assertIn("git push", push_res["reason"])

    def test_symbol_and_dataclass_hallucination_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _create_sample_app(tmp_path)

            (tmp_path / "app" / "services" / "billing.py").write_text(
                "from app.domain.models import Invoice, NonExistentHelper\n\n"
                "def bad_call():\n"
                "    return Invoice(amount=10.0, wrong_field=1)\n",
                encoding="utf-8",
            )
            errs = check_symbol_and_dataclass_resolution(tmp_path)
            joined = "\n".join(errs)
            self.assertIn("[Hallucinated Import]", joined)
            self.assertIn("NonExistentHelper", joined)
            self.assertIn("[Hallucinated Dataclass Field]", joined)
            self.assertIn("wrong_field", joined)

    def test_anti_test_tampering_and_contract_regression_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)
            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=str(tmp_path), check=True, capture_output=True)

            (tmp_path / "app" / "domain" / "models.py").write_text(
                "from dataclasses import dataclass\n\n@dataclass\nclass Invoice:\n    currency: str\n",
                encoding="utf-8",
            )
            (tmp_path / "tests" / "test_billing.py").write_text(
                "import unittest\n\n"
                "class TestBilling(unittest.TestCase):\n"
                "    @unittest.skip('later')\n"
                "    def test_other(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            cfg = {"enforce_contract_compatibility": True, "max_layers_touched": 1, "layers": [
                {"path": "app/domain"}, {"path": "app/services"}
            ]}
            (tmp_path / "app" / "services" / "billing.py").write_text("x = 1\n", encoding="utf-8")

            errs = check_git_head_regressions(tmp_path, cfg)
            joined = "\n".join(errs)
            self.assertIn("[Collateral Contract Regression]", joined)
            self.assertIn("amount", joined)
            self.assertIn("[Test Tampering Blocked]", joined)
            self.assertIn("test_charge", joined)
            self.assertIn("[Blast Radius Exceeded]", joined)

    def test_diff_scoped_ast_micro_mutation_testing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)
            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=str(tmp_path), check=True, capture_output=True)

            billing_path = tmp_path / "app" / "services" / "billing.py"
            billing_path.write_text(
                "from app.domain.models import Invoice\n\n"
                "def charge(inv: Invoice) -> bool:\n"
                "    return inv.amount > 0\n\n"
                "def is_vip(code: str) -> bool:\n"
                "    return code == 'VIP'\n",
                encoding="utf-8",
            )
            test_cmd = f"{sys.executable} -m unittest discover -s tests -p 'test_*.py'"
            mut_errs = run_diff_mutation_check(tmp_path, test_cmd)
            self.assertTrue(mut_errs)
            self.assertIn("[Weak Test / Surviving Mutant]", mut_errs[0])

            test_path = tmp_path / "tests" / "test_billing.py"
            test_path.write_text(
                test_path.read_text(encoding="utf-8")
                + "    def test_is_vip(self):\n"
                + "        from app.services.billing import is_vip\n"
                + "        self.assertTrue(is_vip('VIP'))\n"
                + "        self.assertFalse(is_vip('REG'))\n",
                encoding="utf-8",
            )
            self.assertEqual(run_diff_mutation_check(tmp_path, test_cmd), [])

    # ------------------------------------------------------------------
    # Silent Gaps Audit Regression Tests (GAP-01 through GAP-08)
    # ------------------------------------------------------------------

    def test_gap01_mutation_isolation_crash_safety_and_infinite_loop_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)
            cfg_path = tmp_path / ".ai-native.json"
            cfg_init = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg_init["mutation_testing"] = False
            cfg_path.write_text(json.dumps(cfg_init, indent=2), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=str(tmp_path), check=True, capture_output=True)

            # 1. Crash-safety & isolation: verify live working-tree bytes remain untouched even while mutant runs
            billing_path = tmp_path / "app" / "services" / "billing.py"
            billing_path.write_text(
                "from app.domain.models import Invoice\n\n"
                "def charge(inv: Invoice) -> bool:\n"
                "    return inv.amount > 0\n\n"
                "def spin_until(stop_flag: bool) -> int:\n"
                "    while stop_flag == False:\n"
                "        pass\n"
                "    return 1\n",
                encoding="utf-8",
            )
            orig_bytes = billing_path.read_bytes()
            orig_sha = hashlib.sha256(orig_bytes).hexdigest()

            marker_file = tmp_path / "observed_cwd.txt"
            probe_cmd = (
                f"{sys.executable} -c "
                f"\"import os, pathlib, sys; "
                f"pathlib.Path({repr(str(marker_file))}).write_text(os.getcwd()); "
                f"sys.exit(0 if pathlib.Path({repr(str(billing_path.resolve()))}).read_bytes() == "
                f"pathlib.Path('app/services/billing.py').read_bytes() else 1)\""
            )
            # If isolated properly, live file bytes STILL equal orig_bytes while tempdir file was mutated -> exit 1 (killed)
            # Wait: also verify cwd != live workspace root!
            run_diff_mutation_check(tmp_path, probe_cmd)
            self.assertEqual(hashlib.sha256(billing_path.read_bytes()).hexdigest(), orig_sha)
            self.assertNotEqual(Path(marker_file.read_text()).resolve(), tmp_path.resolve())

            # 2. Infinite loop mutant timeout: spin_until(True) passes normally, loops forever when == becomes !=
            test_path = tmp_path / "tests" / "test_billing.py"
            test_path.write_text(
                test_path.read_text(encoding="utf-8")
                + "    def test_spin(self):\n"
                + "        from app.services.billing import spin_until\n"
                + "        self.assertEqual(spin_until(True), 1)\n",
                encoding="utf-8",
            )
            test_cmd = f"{sys.executable} -m unittest discover -s tests -p 'test_*.py'"
            timeout_errs = run_diff_mutation_check(tmp_path, test_cmd, max_mutants=1, timeout_sec=0.2)
            self.assertEqual(timeout_errs, [])
            self.assertEqual(hashlib.sha256(billing_path.read_bytes()).hexdigest(), orig_sha)

            # 3. Configurable max_mutants cap (commit spin_until, then add untested comparisons)
            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "spin"], cwd=str(tmp_path), check=True, capture_output=True)
            billing_path.write_text(
                billing_path.read_text(encoding="utf-8")
                + "\ndef many_cmp(a, b, c, d):\n"
                + "    return (a == 1) and (b == 2) and (c == 3) and (d == 4)\n",
                encoding="utf-8",
            )
            capped = run_diff_mutation_check(tmp_path, test_cmd, max_mutants=2, timeout_sec=1.0)
            self.assertEqual(len(capped), 2)
            cfg_path = tmp_path / ".ai-native.json"
            cfg_obj = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg_obj["max_mutants"] = 2
            cfg_obj["mutation_testing"] = True
            cfg_path.write_text(json.dumps(cfg_obj, indent=2), encoding="utf-8")
            via_verify = verify_workspace(tmp_path, run_tests=True)
            self.assertEqual(sum(1 for e in via_verify if "[Weak Test / Surviving Mutant]" in e), 2)

    def test_gap02_pre_tool_shell_gate_bypasses_and_config_tampering(self):
        bypass_commands = [
            'git commit -n -m "msg"',
            'git commit -am "msg" -n',
            'git commit -amn "msg"',
            'git commit -anm "msg"',
            'git commit --no-verify -m "msg"',
            'true && git commit -n -m "bypass"',
            'git -c core.hooksPath=/dev/null commit -m "bypass"',
            'git -ccore.hooksPath=/dev/null push origin main',
            'git config core.hooksPath /dev/null',
            'git push --no-verify origin main',
            'git merge -n feature-branch',
            'git cherry-pick --no-verify abc1234',
            'git rebase --no-verify main',
            'chmod -x .githooks/pre-commit',
            'chmod 644 .githooks/pre-push',
            'rm -rf .githooks',
        ]
        for cmd in bypass_commands:
            with self.subTest(blocked=cmd):
                reason, _ = validate_shell_command(cmd)
                self.assertIsNotNone(reason, f"Expected bypass command to be BLOCKED: {cmd}")

        valid_commands = [
            'git commit -m "fix -n flag and --no-verify handling"',
            'git commit -am "document -n behavior"',
            'git commit -m"done with -n spacing"',
            'git clean -n',
            'git clean -nd',
            'git log -n 5',
            'git status && git diff',
            'chmod +x .githooks/pre-commit',
        ]
        for cmd in valid_commands:
            with self.subTest(allowed=cmd):
                reason, _ = validate_shell_command(cmd)
                self.assertIsNone(reason, f"Expected legitimate command to be ALLOWED: {cmd} -> {reason}")

        # Also verify check_git_head_regressions catches .ai-native.json weakening & .githooks tampering
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)
            cfg = {
                "version": "3.0",
                "context_budget": {"max_lines": 200, "max_bytes": 20000},
                "test_command": "",
                "enforce_contract_compatibility": True,
                "mutation_testing": True,
                "max_layers_touched": 1,
                "layers": [{"path": "app/domain", "forbidden_imports": ["requests"]}],
            }
            (tmp_path / ".ai-native.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)

            weak_cfg = {
                "version": "3.0",
                "context_budget": {"max_lines": 500, "max_bytes": 20000},
                "test_command": "",
                "enforce_contract_compatibility": False,
                "mutation_testing": False,
                "max_layers_touched": 5,
                "layers": [{"path": "app/domain", "forbidden_imports": []}],
            }
            (tmp_path / ".ai-native.json").write_text(json.dumps(weak_cfg, indent=2), encoding="utf-8")
            (tmp_path / ".githooks" / "pre-commit").chmod(0o644)

            errs = check_git_head_regressions(tmp_path, weak_cfg)
            joined = "\n".join(errs)
            self.assertIn("[Guardrail Tampering Blocked]", joined)
            self.assertIn("mutation_testing", joined)
            self.assertIn("enforce_contract_compatibility", joined)
            self.assertIn("forbidden_imports", joined)
            self.assertIn("pre-commit", joined)

    def test_gap03_polyglot_comment_stripping_multiline_and_boundary_linting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            web_dir = tmp_path / "web" / "domain"
            web_dir.mkdir(parents=True)
            (web_dir / "AGENTS.md").write_text(f"# Web\n\n{START_MARKER}\n{END_MARKER}\n", encoding="utf-8")
            (tmp_path / "AGENTS.md").write_text(f"# Root\n\n{START_MARKER}\n{END_MARKER}\n", encoding="utf-8")

            ts_file = web_dir / "order.ts"
            ts_file.write_text(
                '/*\n * export function fakeCommentedOut() {}\n */\n'
                '// export const fakeSingleLine = 1;\n'
                'const strSample = "export class FakeInString {}";\n'
                '// import { readFile } from "fs";\n'
                'import axios from "axios";\n'
                'export async function fetchOrder<\n'
                '  T extends { id: string }\n'
                '>(\n'
                '  id: string\n'
                '): Promise<T> {\n'
                '  return {} as T;\n'
                '}\n'
                'export {\n  OrderId,\n  OrderStatus as StatusType\n};\n',
                encoding="utf-8",
            )
            (web_dir / "worker.go").write_text(
                'package domain\n/* func FakeGo() {} */\n// type FakeType struct {}\nfunc ProcessOrder(id string) {}\ntype OrderConfig struct {}\n',
                encoding="utf-8",
            )
            (web_dir / "lib.rs").write_text(
                '// pub fn fake_rust() {}\npub async fn execute_trade() {}\npub struct TradeBook {}\n',
                encoding="utf-8",
            )
            (web_dir / "Gateway.java").write_text(
                '/* public class FakeJava {} */\npublic class PaymentGateway {}\n',
                encoding="utf-8",
            )

            ts_exports = inspect_polyglot_file(ts_file, tmp_path)["exports"]
            self.assertEqual(ts_exports, ["fetchOrder", "OrderId", "StatusType"])
            self.assertEqual(inspect_polyglot_file(web_dir / "worker.go", tmp_path)["exports"], ["ProcessOrder", "OrderConfig"])
            self.assertEqual(inspect_polyglot_file(web_dir / "lib.rs", tmp_path)["exports"], ["execute_trade", "TradeBook"])
            self.assertEqual(inspect_polyglot_file(web_dir / "Gateway.java", tmp_path)["exports"], ["PaymentGateway"])

            (tmp_path / ".ai-native.json").write_text(
                json.dumps({
                    "version": "3.0",
                    "layers": [{"path": "web/domain", "forbidden_imports": ["fs", "axios"]}],
                }),
                encoding="utf-8",
            )
            sync_workspace(tmp_path)
            errs = verify_workspace(tmp_path, run_tests=False)
            joined = "\n".join(errs)
            self.assertIn("axios", joined)
            self.assertNotIn("fs", joined)

    def test_gap04_python_resolver_inheritance_init_false_relative_imports_reexports_and_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            domain_dir = tmp_path / "app" / "domain"
            services_dir = tmp_path / "app" / "services"
            domain_dir.mkdir(parents=True)
            services_dir.mkdir(parents=True)
            (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
            (domain_dir / "__init__.py").write_text("from .models import Child\n", encoding="utf-8")
            (services_dir / "__init__.py").write_text("", encoding="utf-8")

            (domain_dir / "models.py").write_text(
                "from dataclasses import dataclass, field, KW_ONLY\n"
                "from external_pkg import ExternalBase\n\n"
                "@dataclass\n"
                "class Base:\n"
                "    base_id: int\n\n"
                "@dataclass\n"
                "class Child(Base):\n"
                "    _: KW_ONLY\n"
                "    tag: str = 'ok'\n"
                "    computed: int = field(init=False, default=1)\n\n"
                "@dataclass\n"
                "class ExtChild(ExternalBase):\n"
                "    local_field: str\n",
                encoding="utf-8",
            )

            # Valid usage via __init__.py re-export, relative import, inherited base_id, external base
            (services_dir / "good.py").write_text(
                "from ..domain import Child\n"
                "from ..domain.models import ExtChild\n"
                "import app.domain.models as dm\n\n"
                "def ok():\n"
                "    c1 = Child(base_id=1, tag='a')\n"
                "    c2 = dm.Child(base_id=2)\n"
                "    e = ExtChild(local_field='x', parent_ext_kw=99)\n"
                "    return (c1, c2, e)\n",
                encoding="utf-8",
            )
            self.assertEqual(check_symbol_and_dataclass_resolution(tmp_path), [])

            # Invalid usages: init=False field passed, relative hallucinated import, module alias nonexistent fn
            (services_dir / "bad.py").write_text(
                "from ..domain import Child, MissingExport\n"
                "import app.domain.models as dm\n\n"
                "def broken():\n"
                "    dm.does_not_exist()\n"
                "    Child(base_id=1, computed=99)\n"
                "    return Child(base_id=1, bogus=2)\n",
                encoding="utf-8",
            )
            errs = check_symbol_and_dataclass_resolution(tmp_path)
            joined = "\n".join(errs)
            self.assertIn("[Hallucinated Import]", joined)
            self.assertIn("MissingExport", joined)
            self.assertIn("[Hallucinated Symbol]", joined)
            self.assertIn("dm.does_not_exist", joined)
            self.assertIn("computed", joined)
            self.assertIn("bogus", joined)

    def test_gap05_adr_frontmatter_crlf_body_dividers_colons_and_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            adr_dir = tmp_path / "docs" / "adr"
            adr_dir.mkdir(parents=True)

            block_adr = adr_dir / "002-crlf.md"
            block_adr.write_bytes(
                b"---\r\n"
                b"id: ADR-002\r\n"
                b'title: "ADR-02: Retry Policy: Strict"\r\n'
                b"status: accepted\r\n"
                b"applies_to:\r\n"
                b"  - app/domain/*\r\n"
                b"  - app/services/*\r\n"
                b'summary: "Retries: must be idempotent"\r\n'
                b"---\r\n"
                b"# Body\r\n"
                b"---\r\n"
                b"title: Fake Body Divider Title\r\n"
                b"---\r\n"
            )
            meta = parse_adr_frontmatter(block_adr, tmp_path)
            self.assertEqual(meta["id"], "ADR-002")
            self.assertEqual(meta["title"], "ADR-02: Retry Policy: Strict")
            self.assertEqual(meta["summary"], "Retries: must be idempotent")
            self.assertEqual(meta["applies_to_list"], ["app/domain/*", "app/services/*"])
            self.assertTrue(adr_matches_directory(meta, "app/domain"))
            self.assertTrue(adr_matches_directory(meta, "app/services"))
            self.assertFalse(adr_matches_directory(meta, "app/ui"))

            inline_adr = adr_dir / "003-inline.md"
            inline_adr.write_text(
                "---\nid: ADR-003\napplies_to: [app/api/*, 'app/workers/*']\nsummary: API & Workers\n---\n\nText\n---\nMore\n",
                encoding="utf-8",
            )
            meta3 = parse_adr_frontmatter(inline_adr, tmp_path)
            self.assertEqual(meta3["applies_to_list"], ["app/api/*", "app/workers/*"])
            self.assertTrue(adr_matches_directory(meta3, "app/workers"))
            self.assertFalse(adr_matches_directory(meta3, "app/domain"))

    def test_gap06_stop_gate_whitespace_bypass_private_edits_and_reentry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)
            cfg_p = tmp_path / ".ai-native.json"
            cfg_obj = json.loads(cfg_p.read_text(encoding="utf-8"))
            cfg_obj["mutation_testing"] = False
            cfg_p.write_text(json.dumps(cfg_obj), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=str(tmp_path), check=True, capture_output=True)

            # (a) Private helper edit only -> must NOT trigger semantic drift refusal
            billing_path = tmp_path / "app" / "services" / "billing.py"
            orig_billing = billing_path.read_text(encoding="utf-8")
            billing_path.write_text(orig_billing + "\ndef _private_helper(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )

            # (b) Public contract change + whitespace-only edit in AGENTS.md -> MUST still block
            billing_path.write_text(
                orig_billing + "\ndef issue_credit(inv_id: str) -> bool:\n    return len(inv_id) > 0\n",
                encoding="utf-8",
            )
            test_path = tmp_path / "tests" / "test_billing.py"
            test_path.write_text(
                test_path.read_text(encoding="utf-8")
                + "    def test_issue_credit(self):\n"
                + "        from app.services.billing import issue_credit\n"
                + "        self.assertTrue(issue_credit('A'))\n"
                + "        self.assertFalse(issue_credit(''))\n",
                encoding="utf-8",
            )
            svc_agents = tmp_path / "app" / "services" / "AGENTS.md"
            svc_agents.write_text(
                svc_agents.read_text(encoding="utf-8").replace("## Purpose & Invariants\n", "## Purpose & Invariants\n\n   \n"),
                encoding="utf-8",
            )
            sync_workspace(tmp_path)
            drifted = check_semantic_drift(tmp_path)
            self.assertTrue(drifted)
            self.assertIn("app/services/AGENTS.md", drifted[0])
            self.assertIn("issue_credit", drifted[0])

            # (c) Re-entry flags must exit cleanly to prevent infinite refusal loop
            self.assertEqual(_run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "stop_hook_active": True}), {})
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "continuationActive": True, "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )

    def test_gap07_non_destructive_onboarding_preserves_existing_hooks_claude_md_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)

            # Pre-existing custom .git/hooks/pre-commit and custom CLAUDE.md
            ran_marker = tmp_path / "legacy_hook_ran.txt"
            old_hook = tmp_path / ".git" / "hooks" / "pre-commit"
            old_hook.write_text(f"#!/usr/bin/env sh\ntouch {ran_marker}\n", encoding="utf-8")
            old_hook.chmod(0o755)
            (tmp_path / "CLAUDE.md").write_text("# Legacy Rules\nAlways validate tax_id before charge().\n", encoding="utf-8")

            actions = bootstrap_repository(tmp_path)
            joined_actions = "\n".join(actions)
            self.assertIn("Merged existing CLAUDE.md into AGENTS.md", joined_actions)
            self.assertIn("pre-commit.local", joined_actions)
            self.assertIn("Always validate tax_id before charge().", (tmp_path / "AGENTS.md").read_text(encoding="utf-8"))

            local_hook = tmp_path / ".githooks" / "pre-commit.local"
            self.assertTrue(local_hook.exists())
            subprocess.run(["sh", str(tmp_path / ".githooks" / "pre-commit")], cwd=str(tmp_path), check=True, capture_output=True)
            self.assertTrue(ran_marker.exists(), "Pre-existing pre-commit hook was not chained!")

            # Test Windows symlink OSError fallback
            win_path = tmp_path / "win_fallback"
            win_path.mkdir()
            (win_path / "AGENTS.md").write_text("# Win\n", encoding="utf-8")
            with mock.patch("pathlib.Path.symlink_to", side_effect=OSError("WinError 1314")):
                bootstrap_repository(win_path)
            self.assertTrue((win_path / "CLAUDE.md").exists())
            self.assertFalse((win_path / "CLAUDE.md").is_symlink())

            # Test non-destructive JSON settings merger
            settings_path = tmp_path / "settings.json"
            settings_path.write_text(json.dumps({
                "model": "custom-model",
                "hooks": {"PreToolUse": [{"matcher": "CustomTool", "hooks": []}]},
            }), encoding="utf-8")
            self.assertTrue(merge_hooks_into_settings(settings_path))
            self.assertFalse(merge_hooks_into_settings(settings_path))  # idempotent second pass
            merged = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["model"], "custom-model")
            self.assertEqual(len(merged["hooks"]["PreToolUse"]), 2)
            self.assertIn("PostToolUse", merged["hooks"])
            self.assertIn("Stop", merged["hooks"])

    def test_gap08_multi_agent_hook_payload_resilience(self):
        cli_payloads = [
            "null",
            json.dumps({"cwd": "/nonexistent", "tool_name": "run_shell_command", "tool_input": {"command": "echo hi"}}),
        ]
        inproc_payloads = [
            "",
            "[1, 2, 3]",
            "{broken-json",
            json.dumps({"cwd": "/nonexistent", "tool_name": "Bash", "tool_input": "not-a-dict"}),
            json.dumps({"workspacePaths": "invalid-string", "toolCall": {"name": "run_command", "args": {"CommandLine": "echo hi"}}}),
        ]
        hook_modules = (
            ("pre_tool_hook.py", pre_tool_hook),
            ("post_tool_hook.py", post_tool_hook),
            ("stop_gate_hook.py", stop_gate_hook),
        )
        for script_name, mod in hook_modules:
            script_path = SCRIPTS_DIR / script_name
            for raw_in in cli_payloads:
                with self.subTest(mode="cli", script=script_name, payload=raw_in):
                    proc = subprocess.run(
                        [sys.executable, "-I", str(script_path)],
                        input=raw_in,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(proc.returncode, 0, f"{script_name} crashed on {raw_in!r}: {proc.stderr}")
                    parsed = json.loads(proc.stdout)
                    self.assertIsInstance(parsed, dict)
            for raw_in in inproc_payloads:
                with self.subTest(mode="inproc", script=script_name, payload=raw_in):
                    buf_out = io.StringIO()
                    with mock.patch("sys.stdin", io.StringIO(raw_in)), mock.patch("sys.stdout", buf_out):
                        mod.main()
                    parsed = json.loads(buf_out.getvalue())
                    self.assertIsInstance(parsed, dict)

        hooks_cfg = json.loads((REPO_ROOT / "hooks.json").read_text(encoding="utf-8"))["ai-native-auto-sync"]
        self.assertGreaterEqual(hooks_cfg["PreToolUse"][0]["hooks"][0]["timeout"], 60)
        self.assertGreaterEqual(hooks_cfg["Stop"][0]["timeout"], 60)
        self.assertIn("edit_file", hooks_cfg["PreToolUse"][0]["matcher"])

    def test_reverse_index_used_by_tested_by_and_context_capsule(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)

            used_by, tested_by = build_reverse_index(tmp_path)
            self.assertEqual(used_by.get("app/domain/models.py"), ["app/services/billing.py"])
            self.assertIn("tests/test_billing.py::test_charge", tested_by.get("app/services/billing.py", []))

            domain_agents = (tmp_path / "app" / "domain" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("- **Used By:** `app/services/billing.py`", domain_agents)

            services_agents = (tmp_path / "app" / "services" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("- **Tested By:** `tests/test_billing.py::test_charge`", services_agents)

            capsule = build_context_capsule(tmp_path, "app/services/billing.py", mark_ledger=False)
            self.assertIn("## 1. Governing Layer Guidance (`app/services/AGENTS.md`)", capsule)
            self.assertIn("## 2. Architectural Rationale & Invariants", capsule)
            self.assertIn("## Context & Why", capsule)
            self.assertIn("- **Tested By (Verify With):** `tests/test_billing.py::test_charge`", capsule)

            proc = subprocess.run(
                [sys.executable, "-I", str(SCRIPTS_DIR / "sync_context.py"), "--workspace", str(tmp_path), "--for", "app/domain/models.py"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("- **Used By (Blast Radius):** `app/services/billing.py`", proc.stdout)

    def test_pre_edit_context_gate_injects_capsule_once_then_allows(self):
        # 1. Cold edit is blocked once with Context Capsule injected, then unlocked immediately
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)

            cold_edit = _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "toolCall": {"name": "edit_file", "args": {"Path": str(tmp_path / "app" / "services" / "billing.py")}},
            })
            self.assertEqual(cold_edit["decision"], "deny")
            self.assertIn("[ai-native-repo Pre-Edit Context Gate]", cold_edit["reason"])
            self.assertIn("Context & Why", cold_edit["reason"])
            self.assertIn("tests/test_billing.py::test_charge", cold_edit["reason"])

            retry_edit = _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "toolCall": {"name": "edit_file", "args": {"Path": str(tmp_path / "app" / "services" / "billing.py")}},
            })
            self.assertEqual(retry_edit, {"decision": "allow"})

        # 2. Reading governing AGENTS.md + ADRs first via Read tool unlocks edit on first try (Claude Code schema)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)

            _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "tool_name": "Read",
                "tool_input": {"file_path": str(tmp_path / "app" / "services" / "AGENTS.md")},
            })
            # Still missing ADR -> should block once
            partial_edit = _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "tool_name": "Edit",
                "tool_input": {"file_path": str(tmp_path / "app" / "services" / "billing.py")},
            })
            self.assertEqual(partial_edit["decision"], "block")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)

            for req in ("app/services/AGENTS.md", "docs/adr/001-ai-native-architecture.md"):
                _run_hook_inproc(pre_tool_hook, {
                    "cwd": str(tmp_path),
                    "tool_name": "Read",
                    "tool_input": {"file_path": str(tmp_path / req)},
                })
            first_edit = _run_hook_inproc(pre_tool_hook, {
                "cwd": str(tmp_path),
                "tool_name": "Edit",
                "tool_input": {"file_path": str(tmp_path / "app" / "services" / "billing.py")},
            })
            self.assertEqual(first_edit, {})

    def test_adr_completeness_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            adr_dir = tmp_path / "docs" / "adr"
            adr_dir.mkdir(parents=True)
            bad_adr = adr_dir / "002-shallow.md"
            bad_adr.write_text(
                "---\nid: ADR-002\ntitle: Shallow ADR\napplies_to: *\nsummary: Brief.\n---\n\n# Only a title\nJust prose.\n",
                encoding="utf-8",
            )
            errs = check_adr_completeness(tmp_path)
            self.assertEqual(len(errs), 1)
            self.assertIn("[Incomplete ADR Rationale]", errs[0])

            bad_adr.write_text(
                "---\nid: ADR-002\ntitle: Complete ADR\napplies_to: *\nsummary: Full rationale.\n---\n\n"
                "## Context & Why\nEmpirical data showed 99% were cases.\n\n"
                "## Decision & Invariants\nPreserve raw note.\n\n"
                "## Anti-Patterns / Do Not Do\nDo not batch across items.\n",
                encoding="utf-8",
            )
            self.assertEqual(check_adr_completeness(tmp_path), [])

    def test_friction_and_opportunity_detector_all_four_rules(self):
        # 0 & Rule 1: Normal single edit stays silent; rework across cycles without ADR edit nudges once
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)
            cfg_p = tmp_path / ".ai-native.json"
            cfg_obj = json.loads(cfg_p.read_text(encoding="utf-8"))
            cfg_obj["mutation_testing"] = False
            cfg_p.write_text(json.dumps(cfg_obj), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)

            # Single normal edit -> Stop must remain completely silent
            _run_hook_inproc(post_tool_hook, {
                "cwd": str(tmp_path),
                "toolCall": {"name": "edit_file", "args": {"Path": str(tmp_path / "app" / "services" / "billing.py")}},
            })
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )

            # Rework after Stop/test cycle without updating docs/adr/ -> fires Rule 1 once
            _run_hook_inproc(post_tool_hook, {
                "cwd": str(tmp_path),
                "toolCall": {"name": "edit_file", "args": {"Path": str(tmp_path / "app" / "services" / "billing.py")}},
            })
            nudge = _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"})
            self.assertEqual(nudge["decision"], "continue")
            self.assertIn("[ai-native-repo Friction Detector — Uncaptured Course-Correction]", nudge["reason"])
            self.assertIn("app/services/billing.py", nudge["reason"])

            # Immediate retry allows due to cooldown
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )

        # Rule 2: Context Thrashing (>=8 exploratory reads before edit in un-onboarded Git repo)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            for i in range(7):
                _run_hook_inproc(post_tool_hook, {
                    "cwd": str(tmp_path),
                    "toolCall": {"name": "read_file", "args": {"Path": str(tmp_path / f"f{i}.py")}},
                })
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )
            _run_hook_inproc(post_tool_hook, {
                "cwd": str(tmp_path),
                "tool_name": "Grep",
                "tool_input": {"path": str(tmp_path)},
            })
            thrash = _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"})
            self.assertEqual(thrash["decision"], "continue")
            self.assertIn("[ai-native-repo Opportunity Detector — Context Thrashing]", thrash["reason"])
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )

        # Rule 3: Repeated 3-step command sequence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for _ in range(3):
                for step in ("python3 fetch.py", "python3 validate.py", "python3 export.py"):
                    _run_hook_inproc(post_tool_hook, {
                        "cwd": str(tmp_path),
                        "toolCall": {"name": "run_command", "args": {"CommandLine": step}},
                    })
            wf_nudge = _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"})
            self.assertEqual(wf_nudge["decision"], "continue")
            self.assertIn("[ai-native-repo Opportunity Detector — Repeated Workflow]", wf_nudge["reason"])
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                {"decision": "allow"},
            )

        # Rule 4: Recurring polling / status command
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for _ in range(3):
                _run_hook_inproc(post_tool_hook, {
                    "cwd": str(tmp_path),
                    "tool_name": "Bash",
                    "tool_input": {"command": "gh run list --limit 5"},
                })
            poll_nudge = _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "stop_hook_active": False})
            self.assertEqual(poll_nudge["decision"], "block")
            self.assertIn("[ai-native-repo Opportunity Detector — Automation Candidate]", poll_nudge["reason"])
            self.assertEqual(
                _run_hook_inproc(stop_gate_hook, {"cwd": str(tmp_path), "stop_hook_active": False}),
                {},
            )


if __name__ == "__main__":
    unittest.main()
