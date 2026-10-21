"""Offline CI selection contracts; no dependency installation or cloud calls."""

import json
import unittest
from unittest.mock import patch

from scripts.ci import CONFIG, ROOT, changed_paths, install, pytest_command, select


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG.read_text())
        self.available = {
            str(path.relative_to(ROOT)) for path in (ROOT / "tests").rglob("test_*.py")
        }

    def plan(self, *paths):
        return select(list(paths), self.config, self.available)

    def test_documentation_does_not_request_a_runtime_environment(self):
        plan = self.plan("README.md", "docs/guide.md")
        self.assertEqual(plan["tests"], [])
        self.assertFalse(plan["frontend"])

    def test_publication_documentation_gate_has_no_path_filter(self):
        workflow = (ROOT / ".github/workflows/documentation.yaml").read_text()
        self.assertNotIn("paths:", workflow.split("permissions:", 1)[0])
        self.assertIn("branches: [main, dev]", workflow)

    def test_each_runtime_python_file_selects_offline_tests(self):
        for path in (ROOT / "custom_components/ninebot").glob("*.py"):
            with self.subTest(path=path.name):
                self.assertTrue(self.plan(str(path.relative_to(ROOT)))["tests"])

    def test_new_unmapped_runtime_is_conservative(self):
        plan = self.plan("custom_components/ninebot/future_protocol.py")
        self.assertEqual(set(plan["tests"]), self.available)
        self.assertIn("unmapped-runtime", plan["reasons"])

    def test_live_paths_and_new_polling_modules_select_the_live_contract(self):
        for name in ("realtime", "query_broker", "const", "control_results", "polling_policy"):
            with self.subTest(name=name):
                plan = self.plan(f"custom_components/ninebot/{name}.py")
                self.assertIn("tests/test_coordinator.py", plan["tests"])
                self.assertIn("tests/test_control_polling.py", plan["tests"])

    def test_overlapping_scopes_are_unioned_once(self):
        plan = self.plan(
            "custom_components/ninebot/coordinator.py", "custom_components/ninebot/daily_sync.py"
        )
        self.assertEqual(len(plan["tests"]), len(set(plan["tests"])))
        self.assertIn("tests/test_statistics_continuity.py", plan["tests"])

    def test_critical_configuration_keeps_both_boundaries(self):
        plan = self.plan("custom_components/ninebot/config_flow.py")
        self.assertEqual(
            [row["boundary"] for row in plan["matrix"]["include"]], ["minimum", "current"]
        )

    def test_frontend_and_shared_test_inputs(self):
        self.assertTrue(self.plan("custom_components/ninebot/frontend/route-map.js")["frontend"])
        self.assertEqual(self.plan("custom_components/ninebot/frontend/route-map.js")["tests"], [])
        for path in ("tests/conftest.py", "tests/fixtures/example.json", "pyproject.toml"):
            self.assertEqual(set(self.plan(path)["tests"]), self.available)

    def test_changed_test_and_ci_self_changes(self):
        plan = select(
            ["tests/test_future.py"], self.config, self.available | {"tests/test_future.py"}
        )
        self.assertIn("tests/test_future.py", plan["tests"])
        for path in ("scripts/ci.py", ".github/ci.json", ".github/workflows/runtime.yaml"):
            self.assertEqual(set(self.plan(path)["tests"]), set(self.config["pipeline_tests"]))

    def test_group_files_exist_and_dependency_pin_matches_runtime(self):
        for group in self.config["groups"].values():
            self.assertLessEqual(set(group["tests"]), self.available)
        self.assertLessEqual(set(self.config["pipeline_tests"]), self.available)
        manifest = json.loads((ROOT / "custom_components/ninebot/manifest.json").read_text())
        self.assertIn(f"ninecli=={self.config['tools']['ninecli']}", manifest["requirements"])
        for row in self.config["boundaries"].values():
            self.assertEqual(set(row), {"python", "ha", "plugin"})

    def test_complete_selected_suite_collects_coverage_without_a_second_run(self):
        command = pytest_command(sorted(self.available), self.available)
        self.assertIn("--cov-fail-under=91", command)
        self.assertIn("--cov-report=xml", command)
        self.assertEqual(command.count("-m"), 1)
        partial = pytest_command(["tests/test_coordinator.py"], self.available)
        self.assertFalse(any(arg.startswith("--cov") for arg in partial))

    def test_test_runner_rejects_empty_or_unavailable_inputs(self):
        for tests in ([], ["tests/not-present.py"]):
            with self.assertRaises(ValueError):
                pytest_command(tests, self.available)

    @patch("scripts.ci.subprocess.check_output")
    def test_renames_and_deletions_keep_both_paths(self, output):
        output.return_value = (
            b"custom_components/ninebot/old.py\0custom_components/ninebot/new.py\0"
        )
        self.assertEqual(len(changed_paths("a" * 40, "b" * 40)), 2)
        self.assertIn("--no-renames", output.call_args.args[0])
        with self.assertRaises(ValueError):
            changed_paths("dev; ignored", "b" * 40)

    @patch("scripts.ci.version")
    @patch("scripts.ci.subprocess.run")
    def test_install_checks_the_exact_boundary_and_dependency_consistency(self, run, installed):
        selected = self.config["boundaries"]["current"]
        installed.side_effect = lambda name: {
            "homeassistant": selected["ha"],
            "pytest-homeassistant-custom-component": selected["plugin"],
            **self.config["tools"],
        }[name]
        install(self.config, "current")
        self.assertIn(f"homeassistant=={selected['ha']}", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["pip", "check"])
        installed.return_value = "incorrect"
        installed.side_effect = None
        with self.assertRaises(ValueError):
            install(self.config, "current")


if __name__ == "__main__":
    unittest.main()
