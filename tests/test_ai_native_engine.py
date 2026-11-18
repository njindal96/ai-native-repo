import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from bootstrap_repo import bootstrap_repository  # noqa: E402
from stop_gate_hook import split_semantic_and_auto  # noqa: E402
from sync_context import END_MARKER, START_MARKER, sync_workspace  # noqa: E402
from verify_architecture import (  # noqa: E402
    check_git_head_regressions,
    check_symbol_and_dataclass_resolution,
    run_diff_mutation_check,
    verify_workspace,
)


def _init_git_repo(root: Path):
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(root), check=True, capture_output=True)


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

            subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=str(tmp_path), check=True, capture_output=True)

            post_proc = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "post_tool_hook.py")],
                input=json.dumps({"cwd": str(tmp_path)}),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(post_proc.stdout.strip(), "{}")

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

            agy_stop = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "stop_gate_hook.py")],
                input=json.dumps({"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                text=True,
                capture_output=True,
                check=True,
            )
            agy_res = json.loads(agy_stop.stdout)
            self.assertEqual(agy_res["decision"], "continue")
            self.assertIn("app/services/AGENTS.md", agy_res["reason"])

            cc_stop = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "stop_gate_hook.py")],
                input=json.dumps({"cwd": str(tmp_path), "stop_hook_active": False}),
                text=True,
                capture_output=True,
                check=True,
            )
            cc_res = json.loads(cc_stop.stdout)
            self.assertEqual(cc_res["decision"], "block")
            self.assertIn("app/services/AGENTS.md", cc_res["reason"])

            svc_agents = tmp_path / "app" / "services" / "AGENTS.md"
            svc_text = svc_agents.read_text(encoding="utf-8").replace(
                "## Purpose & Invariants\n",
                "## Purpose & Invariants\n- Supports refunds via `refund()`.\n",
            )
            svc_agents.write_text(svc_text, encoding="utf-8")

            agy_allow = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "stop_gate_hook.py")],
                input=json.dumps({"cwd": str(tmp_path), "terminationReason": "NO_TOOL_CALL"}),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(agy_allow.stdout), {"decision": "allow"})

            cc_allow = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "stop_gate_hook.py")],
                input=json.dumps({"cwd": str(tmp_path), "stop_hook_active": False}),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(cc_allow.stdout), {})

    def test_pre_tool_hook_blocks_bypass_and_unverified_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            _create_sample_app(tmp_path)
            bootstrap_repository(tmp_path)

            # Antigravity & Claude Code --no-verify denial
            agy_deny = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "pre_tool_hook.py")],
                input=json.dumps({
                    "cwd": str(tmp_path),
                    "toolCall": {"name": "run_command", "args": {"CommandLine": "git commit --no-verify -m x"}},
                }),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(agy_deny.stdout)["decision"], "deny")

            cc_deny = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "pre_tool_hook.py")],
                input=json.dumps({
                    "cwd": str(tmp_path),
                    "tool_name": "Bash",
                    "tool_input": {"command": "git commit -n -m x"},
                }),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(cc_deny.stdout)["decision"], "block")

            # Introduce failing syntax error and attempt git push -> blocked
            (tmp_path / "app" / "services" / "billing.py").write_text("def broken(:\n", encoding="utf-8")
            push_deny = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "pre_tool_hook.py")],
                input=json.dumps({
                    "cwd": str(tmp_path),
                    "toolCall": {"name": "run_command", "args": {"CommandLine": "git push origin main"}},
                }),
                text=True,
                capture_output=True,
                check=True,
            )
            push_res = json.loads(push_deny.stdout)
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

            # Remove a field from Invoice dataclass and delete/weaken test_charge
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

            # Add new function with a comparison that existing tests do NOT assert on
            billing_path = tmp_path / "app" / "services" / "billing.py"
            billing_path.write_text(
                "from app.domain.models import Invoice\n\n"
                "def charge(inv: Invoice) -> bool:\n"
                "    return inv.amount > 0\n\n"
                "def is_vip(code: str) -> bool:\n"
                "    return code == 'VIP'\n",
                encoding="utf-8",
            )
            test_cmd = "python3 -m unittest discover -s tests -p 'test_*.py'"
            mut_errs = run_diff_mutation_check(tmp_path, test_cmd)
            self.assertTrue(mut_errs)
            self.assertIn("[Weak Test / Surviving Mutant]", mut_errs[0])

            # Now add real assertions killing the mutant
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


if __name__ == "__main__":
    unittest.main()
