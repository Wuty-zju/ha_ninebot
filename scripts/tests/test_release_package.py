"""Small synthetic package/publication guards; no HA, cloud or real release writes."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.build_release import build_package, committed_files, verify_package
from scripts.ci_evidence import input_digest, verify_full_run
from scripts.publish_release import APP_ID, REQUIRED_CHECKS, candidate_gate, publish, release_body


class FakeGitHub:
    repository = "synthetic/repository"

    def __init__(self, sha):
        self.sha = sha
        self.current = None
        self.assets = []
        self.tag = None
        self.writes = []
        self.fail_upload = False
        self.latest = {"id": 1}

    def request(self, path, *, method="GET", payload=None, data=None, content_type=None):
        if method != "GET":
            self.writes.append((method, path))
        if path == "actions/runs/42":
            return {
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "path": ".github/workflows/checks.yaml",
                "head_repository": {"full_name": self.repository},
                "head_sha": self.sha,
                "html_url": "https://example.invalid/run",
            }
        if path.startswith("actions/runs/42/jobs?"):
            names = ["Core lint", "HA suite (2026.1.0)", "HA suite (2026.10.2)"]
            return {
                "jobs": [
                    {
                        "id": i,
                        "name": name,
                        "head_sha": self.sha,
                        "status": "completed",
                        "conclusion": "success",
                        "steps": [
                            {"name": step, "conclusion": "success"}
                            for step in (
                                ["Run node --test tests/frontend/*.mjs"]
                                if name == "Core lint"
                                else [
                                    "Install and verify selected boundary",
                                    "Run mypy custom_components/ninebot --follow-imports=silent",
                                    "Run the complete offline suite",
                                ]
                            )
                        ],
                    }
                    for i, name in enumerate(names)
                ]
            }
        if path.startswith("git/ref/heads/"):
            return {"object": {"sha": self.sha}}
        if path.startswith("git/ref/tags/"):
            return {"object": {"sha": self.tag, "type": "commit"}} if self.tag else None
        if path.startswith("commits/"):
            return {
                "check_runs": [
                    {
                        "name": n,
                        "id": i,
                        "app": {"id": APP_ID},
                        "head_sha": self.sha,
                        "status": "completed",
                        "conclusion": "success",
                    }
                    for i, n in enumerate(sorted(REQUIRED_CHECKS))
                ]
            }
        if path == "releases/latest":
            return self.latest
        if path.startswith("releases?per_page"):
            return [self.current] if self.current else []
        if path == "releases" and method == "POST":
            self.current = payload | {"id": 2, "html_url": "https://example.invalid/release"}
            return self.current
        if path.startswith("releases/2/assets"):
            if method == "GET":
                return list(self.assets)
            if self.fail_upload:
                self.fail_upload = False
                raise RuntimeError("Synthetic interrupted upload")
            name = path.split("name=", 1)[1]
            row = {
                "name": name,
                "state": "uploaded",
                "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            }
            self.assets.append(row)
            return row
        if path == "releases/2":
            if method == "PATCH":
                self.current.update(payload)
                self.tag = self.sha
                if payload.get("make_latest") == "true":
                    self.latest = self.current
            return self.current
        raise AssertionError((method, path))


class PackageAndPublication(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Package Test")
        self.git("config", "user.email", "synthetic@example.invalid")
        runtime = self.repo / "custom_components/ninebot"
        runtime.mkdir(parents=True)
        for name, content in {
            "manifest.json": json.dumps({"domain": "ninebot", "version": "2.0.0b95"}),
            "__init__.py": "raise RuntimeError('Candidate must never execute')\n",
            "frontend/card.js": "// Synthetic runtime asset\n",
            "licenses/NOTICE.txt": "Synthetic license\n",
            "translations/zh-Hans.json": "{}",
        }.items():
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (self.repo / "hacs.json").write_text(
            json.dumps({"zip_release": True, "filename": "ninebot.zip"})
        )
        (self.repo / ".github").mkdir()
        (self.repo / ".github/ci.json").write_text(
            json.dumps(
                {"boundaries": {"minimum": {"ha": "2026.1.0"}, "current": {"ha": "2026.10.2"}}}
            )
        )
        self.git("add", ".")
        self.commit()
        self.output = self.root / "out"
        self.api = FakeGitHub(self.sha)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def commit(self):
        self.git("commit", "--quiet", "-m", "Synthetic package candidate")
        self.sha = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/dev", self.sha)

    def run_publish(self, **kwargs):
        return publish(
            self.api,
            self.repo,
            self.output,
            "preview",
            "2.0.0b95",
            "dev",
            self.sha,
            "- Add packaged downloads.",
            "- 增加安装包下载。",
            **kwargs,
        )

    def test_package_uses_exact_git_blobs_and_keeps_all_runtime_assets(self):
        files = committed_files(self.repo, self.sha)
        (self.repo / "custom_components/ninebot/__init__.py").write_text("dirty worktree")
        (self.repo / "custom_components/ninebot/private.json").write_text("not tracked")
        first = build_package(self.repo, self.sha, "2.0.0b95", self.output)
        second = build_package(self.repo, self.sha, "2.0.0b95", self.root / "again")
        self.assertEqual(first["sha256"], second["sha256"])
        with ZipFile(self.output / "ninebot.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(files))
            self.assertNotIn("private.json", archive.namelist())
            self.assertEqual(archive.read("__init__.py"), files["__init__.py"][1])
        self.assertEqual(verify_package(self.output / "ninebot.zip", files), first["sha256"])

    def test_wrong_version_and_missing_zip_configuration_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "domain/version"):
            build_package(self.repo, self.sha, "2.0.0b96", self.output)
        (self.repo / "hacs.json").write_text("{}")
        self.git("add", "hacs.json")
        self.commit()
        with self.assertRaisesRegex(ValueError, "zip_release"):
            build_package(self.repo, self.sha, "2.0.0b95", self.output)

    def test_tracked_symlink_is_rejected(self):
        (self.repo / "custom_components/ninebot/link").symlink_to("manifest.json")
        self.git("add", ".")
        self.commit()
        with self.assertRaisesRegex(ValueError, "links"):
            committed_files(self.repo, self.sha)

    def test_missing_extra_duplicate_and_changed_zip_members_are_rejected(self):
        files = committed_files(self.repo, self.sha)
        for names in (list(files)[1:], [*files, "../escape"], [*files, "manifest.json"]):
            path = self.root / "bad.zip"
            with ZipFile(path, "w") as archive:
                for name in names:
                    archive.writestr(name, b"wrong")
            with self.assertRaises(ValueError):
                verify_package(path, files)

    def test_prepare_makes_no_remote_changes(self):
        report = self.run_publish()
        self.assertEqual(report["status"], "prepared")
        self.assertEqual(self.api.writes, [])

    def test_publish_attaches_verified_assets_before_visibility_and_is_idempotent(self):
        report = self.run_publish(apply=True)
        self.assertEqual(report["status"], "published_verified")
        self.assertFalse(self.api.current["draft"])
        self.assertTrue(self.api.current["prerelease"])
        self.assertEqual(self.api.latest, {"id": 1})
        self.assertEqual(self.api.writes[-1], ("PATCH", "releases/2"))
        count = len(self.api.writes)
        self.run_publish(apply=True)
        self.assertEqual(len(self.api.writes), count)

    def test_stable_and_promoted_rc_set_latest_after_assets_are_verified(self):
        for channel, version in (("stable", "2.0.0"), ("candidate", "2.0.0rc1")):
            with self.subTest(channel=channel):
                manifest = self.repo / "custom_components/ninebot/manifest.json"
                manifest.write_text(json.dumps({"domain": "ninebot", "version": version}))
                self.git("add", ".")
                self.commit()
                self.git("update-ref", "refs/remotes/origin/main", self.sha)
                self.api = FakeGitHub(self.sha)
                report = publish(
                    self.api,
                    self.repo,
                    self.output,
                    channel,
                    version,
                    "main",
                    self.sha,
                    "- Package release.",
                    "- 安装包发布。",
                    apply=True,
                    validation_run=42,
                )
                self.assertEqual(report["status"], "published_verified")
                self.assertEqual(self.api.latest["id"], report["release_id"])
                self.assertFalse(self.api.current["prerelease"])
                self.assertEqual(self.api.current["tag_name"], f"v{version}")

    def test_successful_publication_with_lost_response_is_read_back_without_replay(self):
        original = self.api.request

        def interrupted(path, **kwargs):
            result = original(path, **kwargs)
            if kwargs.get("method") == "PATCH":
                raise RuntimeError("Synthetic response lost after successful publication")
            return result

        self.api.request = interrupted
        self.assertEqual(self.run_publish(apply=True)["status"], "published_verified")
        self.assertEqual(sum(method == "PATCH" for method, _ in self.api.writes), 1)

    def test_interrupted_upload_keeps_draft_and_resumes_without_replacement(self):
        self.api.fail_upload = True
        with self.assertRaises(RuntimeError):
            self.run_publish(apply=True)
        self.assertTrue(self.api.current["draft"])
        self.run_publish(apply=True)
        self.assertEqual(sum(path == "releases" for _, path in self.api.writes), 1)
        self.assertFalse(self.api.current["draft"])

    def test_mismatched_existing_asset_is_never_overwritten(self):
        self.api.fail_upload = True
        with self.assertRaises(RuntimeError):
            self.run_publish(apply=True)
        self.api.assets = [{"name": "ninebot.zip", "state": "uploaded", "size": 3, "digest": "bad"}]
        count = len(self.api.writes)
        with self.assertRaisesRegex(ValueError, "differs"):
            self.run_publish(apply=True)
        self.assertEqual(len(self.api.writes), count)
        self.assertTrue(self.api.current["draft"])

    def test_conflicting_tag_and_changed_source_are_rejected_before_writes(self):
        self.api.tag = "a" * 40
        with self.assertRaisesRegex(ValueError, "Tag already"):
            self.run_publish(apply=True)
        self.api.tag = None
        self.api.sha = "b" * 40
        with self.assertRaisesRegex(ValueError, "source branch tip"):
            self.run_publish(apply=True)
        self.assertEqual(self.api.writes, [])

    def test_failed_or_forged_checks_do_not_authorize_publication(self):
        original = self.api.request
        for changes in ({"conclusion": "failure"}, {"app": {"id": 999}}, {"head_sha": "c" * 40}):

            def changed(path, changes=changes, **kwargs):
                result = original(path, **kwargs)
                if path.startswith("commits/"):
                    result["check_runs"][0].update(changes)
                return result

            self.api.request = changed
            with self.assertRaisesRegex(ValueError, "successful GitHub Actions"):
                candidate_gate(self.api, "dev", self.sha)
        self.assertEqual(self.api.writes, [])

    def test_documentation_check_is_required_before_publication(self):
        original = self.api.request

        def missing_docs(path, **kwargs):
            result = original(path, **kwargs)
            if path.startswith("commits/"):
                result["check_runs"] = [r for r in result["check_runs"] if r["name"] != "docs"]
            return result

        self.api.request = missing_docs
        with self.assertRaisesRegex(ValueError, "successful GitHub Actions"):
            self.run_publish(apply=True)
        self.assertEqual(self.api.writes, [])

    def test_preparation_digest_cannot_change_before_publish(self):
        with self.assertRaisesRegex(ValueError, "preparation job"):
            self.run_publish(apply=True, expected_digest="d" * 64)
        self.assertEqual(self.api.writes, [])

    def test_bilingual_notes_and_changelog_use_previous_published_channel(self):
        catalog = [
            {"tag_name": "v1.2.0", "draft": False, "prerelease": False},
            {"tag_name": "v2.0.0b94", "draft": False, "prerelease": True},
            {"tag_name": "v2.0.0b95", "draft": True, "prerelease": True},
        ]
        preview = release_body("o/r", "v2.0.0b95", "preview", catalog, "- Change", "- 更新")
        stable = release_body("o/r", "v2.0.0", "stable", catalog, "- Change", "- 更新")
        self.assertIn("v2.0.0b94...v2.0.0b95", preview)
        self.assertIn("v1.2.0...v2.0.0", stable)
        candidate = release_body("o/r", "v2.0.0rc1", "candidate", catalog, "- Change", "- 更新")
        self.assertIn("v2.0.0b94...v2.0.0rc1", candidate)
        self.assertIn("<summary>简体中文</summary>", preview)
        for en, zh, upgrade in (
            ("中文", "更新", ""),
            ("Change", "", ""),
            ("Change", "更新", "Upgrade"),
        ):
            with self.assertRaises(ValueError):
                release_body("o/r", "v2.0.0b95", "preview", [], en, zh, upgrade)

    def test_stable_changelog_excludes_promoted_preview_versions(self):
        catalog = [
            {"tag_name": tag, "draft": False, "prerelease": False}
            for tag in ("v1.2.0", "v2.0.0a1", "v2.0.0b106", "v2.0.0rc4", "v2.1.0")
        ]
        catalog.append({"tag_name": "v1.9.0", "draft": True, "prerelease": False})
        stable = release_body("o/r", "v2.0.0", "stable", catalog, "- Change", "- 更新")
        self.assertIn("v1.2.0...v2.0.0", stable)
        for channel, tag, previous in (
            ("candidate", "v2.0.0rc5", "v2.0.0rc4"),
            ("preview", "v2.0.0b107", "v2.0.0b106"),
        ):
            with self.subTest(channel=channel):
                body = release_body("o/r", tag, channel, catalog, "- Change", "- 更新")
                self.assertIn(f"{previous}...{tag}", body)
        catalog.append({"tag_name": "v2.0.0", "draft": False, "prerelease": False})
        patch = release_body("o/r", "v2.0.1", "stable", catalog, "- Change", "- 更新")
        self.assertIn("v2.0.0...v2.0.1", patch)

    def test_first_stable_changelog_does_not_use_an_rc(self):
        catalog = [{"tag_name": "v2.0.0rc4", "draft": False, "prerelease": False}]
        body = release_body("o/r", "v2.0.0", "stable", catalog, "- Change", "- 更新")
        self.assertNotIn("Full Changelog", body)

    def test_full_validation_reuses_docs_but_rejects_changed_runtime(self):
        evidence = verify_full_run(self.api, self.repo, self.sha, 42)
        before = self.sha
        (self.repo / "README.md").write_text("Documentation only")
        self.git("add", ".")
        self.commit()
        self.assertEqual(input_digest(self.repo, before), input_digest(self.repo, self.sha))
        self.assertEqual(
            verify_full_run(self.api, self.repo, self.sha, 42)["input_sha256"],
            evidence["input_sha256"],
        )
        (self.repo / "custom_components/ninebot/new.py").write_text("# Different runtime")
        self.git("add", ".")
        self.commit()
        with self.assertRaisesRegex(ValueError, "inputs differ"):
            verify_full_run(self.api, self.repo, self.sha, 42)

    def test_full_validation_rejects_selected_suite_forks_skips_and_cancelled_runs(self):
        original = self.api.request
        for variant in (
            "selected",
            "fork",
            "cancelled",
            "skipped",
            "wrong_workflow",
            "wrong_event",
            "wrong_commit",
        ):

            def changed(path, variant=variant, **kwargs):
                result = original(path, **kwargs)
                if path == "actions/runs/42":
                    if variant == "fork":
                        result["head_repository"] = {"full_name": "other/fork"}
                    elif variant == "cancelled":
                        result["conclusion"] = "cancelled"
                    elif variant == "wrong_workflow":
                        result["path"] = ".github/workflows/runtime.yaml"
                    elif variant == "wrong_event":
                        result["event"] = "pull_request"
                elif path.startswith("actions/runs/42/jobs?"):
                    if variant == "selected":
                        result["jobs"].pop()
                    elif variant == "skipped":
                        result["jobs"][0]["steps"][0]["conclusion"] = "skipped"
                    elif variant == "wrong_commit":
                        result["jobs"][0]["head_sha"] = "f" * 40
                return result

            self.api.request = changed
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                verify_full_run(self.api, self.repo, self.sha, 42)
        self.api.request = original

    def test_partial_api_responses_retry_reads_but_not_mutations(self):
        import io
        from http.client import IncompleteRead
        from unittest.mock import patch

        from scripts.publish_release import GitHub

        for method, expected_calls in (("GET", 2), ("POST", 1)):
            with (
                patch("scripts.publish_release.time.sleep"),
                patch(
                    "scripts.publish_release.urlopen",
                    side_effect=[IncompleteRead(b"synthetic"), io.BytesIO(b'{"ok":true}')],
                ) as request,
            ):
                api = GitHub("synthetic/repository", "synthetic-token")
                if method == "GET":
                    self.assertEqual(api.request("actions/runs/42"), {"ok": True})
                else:
                    with self.assertRaises(RuntimeError):
                        api.request("releases", method=method, payload={"name": "synthetic"})
                self.assertEqual(request.call_count, expected_calls)

    def test_validation_scope_distinguishes_runtime_ci_from_release_tools(self):
        before = self.sha
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts/publish_release.py").write_text("# publication transport correction")
        self.git("add", ".")
        self.commit()
        self.assertEqual(input_digest(self.repo, before), input_digest(self.repo, self.sha))
        (self.repo / "scripts/ci.py").write_text("# different HA installation or test command")
        self.git("add", ".")
        self.commit()
        self.assertNotEqual(input_digest(self.repo, before), input_digest(self.repo, self.sha))

    def test_validation_fetches_tested_commit_missing_from_a_shallow_checkout(self):
        tested = self.sha
        self.git("branch", "-M", "main")
        (self.repo / "README.md").write_text("Later documentation commit")
        self.git("add", ".")
        self.commit()
        clone = self.root / "shallow"
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-local",
                "--depth=1",
                "--single-branch",
                "--branch",
                "main",
                self.repo.as_uri(),
                str(clone),
            ],
            check=True,
        )
        missing = subprocess.run(
            ["git", "-C", str(clone), "cat-file", "-e", tested], capture_output=True
        )
        self.assertNotEqual(missing.returncode, 0)
        proof = verify_full_run(self.api, clone, self.sha, 42)
        self.assertEqual(proof["head_sha"], tested)
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
            ).strip(),
            self.sha,
        )

    def test_version_and_issue_templates_reuse_runtime_but_dependencies_do_not(self):
        before = self.sha
        manifest_path = self.repo / "custom_components/ninebot/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "2.0.0rc4"
        manifest_path.write_text(json.dumps(manifest))
        issue = self.repo / ".github/ISSUE_TEMPLATE/bug.yml"
        issue.parent.mkdir(parents=True)
        issue.write_text("name: Synthetic issue form\n")
        self.git("add", ".")
        self.commit()
        self.assertEqual(input_digest(self.repo, before), input_digest(self.repo, self.sha))
        manifest["requirements"] = ["synthetic==2.0.0"]
        manifest_path.write_text(json.dumps(manifest))
        self.git("add", ".")
        self.commit()
        self.assertNotEqual(input_digest(self.repo, before), input_digest(self.repo, self.sha))

    def test_executed_workflows_and_ha_boundary_always_invalidate_full_evidence(self):
        for name in ("checks.yaml", "runtime.yaml", "history-browser.yaml", "history-profile.yaml"):
            with self.subTest(name=name):
                before = self.sha
                path = self.repo / ".github/workflows" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# changed execution inputs\n")
                self.git("add", ".")
                self.commit()
                self.assertNotEqual(
                    input_digest(self.repo, before), input_digest(self.repo, self.sha)
                )
        before = self.sha
        (self.repo / ".github/ci.json").write_text('{"changed": true}')
        self.git("add", ".")
        self.commit()
        self.assertNotEqual(input_digest(self.repo, before), input_digest(self.repo, self.sha))

    def test_invalid_manifest_cannot_supply_validation_evidence(self):
        (self.repo / "custom_components/ninebot/manifest.json").write_text("[]")
        self.git("add", ".")
        self.commit()
        with self.assertRaisesRegex(ValueError, "manifest"):
            input_digest(self.repo, self.sha)


if __name__ == "__main__":
    unittest.main()
