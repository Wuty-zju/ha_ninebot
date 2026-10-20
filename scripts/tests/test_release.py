"""Small offline release guards using disposable Git objects, never HA or cloud APIs."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.check_release import check_candidate, validate_request

COMMIT = "a" * 40


class ReleasePolicy(unittest.TestCase):
    def test_channel_and_candidate_branch_contract(self):
        for channel, version, source in (
            ("preview", "2.0.0b95", "dev"),
            ("preview", "2.0.0a1", "dev"),
            ("preview", "2.0.0rc1", "release/2.0.0"),
            ("stable", "2.0.0", "main"),
            ("candidate", "2.0.0rc1", "main"),
        ):
            with self.subTest(channel=channel, source=source):
                validate_request(channel, version, source, COMMIT)

    def test_reject_wrong_channel_source_version_and_shell_text(self):
        for channel, version, source, sha in (
            ("preview", "2.0.0b95", "main", COMMIT),
            ("stable", "2.0.0b95", "main", COMMIT),
            ("preview", "2.0.0", "dev", COMMIT),
            ("stable", "2.0.0", "dev", COMMIT),
            ("preview", "2.0.0rc1", "release/2.1.0", COMMIT),
            ("preview", "v2.0.0b95", "dev", COMMIT),
            ("preview", "2.0.0b095", "dev", COMMIT),
            ("preview", "2.0.0b95", "dev", "main"),
            ("preview", "2.0.0b95", "dev;echo unexpected", COMMIT),
            ("preview", "2.0.0b95\n", "dev", COMMIT),
            ("unknown", "2.0.0b95", "dev", COMMIT),
            ("candidate", "2.0.0b95", "main", COMMIT),
            ("candidate", "2.0.0a1", "main", COMMIT),
            ("candidate", "2.0.0", "main", COMMIT),
            ("candidate", "2.0.0rc1", "dev", COMMIT),
        ):
            with self.subTest(channel=channel, version=version, source=source):
                with self.assertRaises(ValueError):
                    validate_request(channel, version, source, sha)


class CandidateObjects(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Release Test")
        self.git("config", "user.email", "synthetic@example.invalid")
        manifest = self.repo / "custom_components/ninebot/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"domain": "ninebot", "version": "2.0.0b95"}))
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic candidate")
        self.sha = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/dev", self.sha)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.repo), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    def check(self, version="2.0.0b95", sha=None):
        return check_candidate(self.repo, "preview", version, "dev", sha or self.sha)

    def test_reads_committed_version_without_mutating_or_running_candidate(self):
        (self.repo / "custom_components/ninebot/manifest.json").write_text("invalid worktree JSON")
        before = self.git("status", "--porcelain")
        result = self.check()
        self.assertEqual(result["candidate_sha"], self.sha)
        self.assertTrue(result["prerelease"])
        self.assertFalse(result["set_latest"])
        self.assertEqual(result["tree_sha"], self.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(before, self.git("status", "--porcelain"))

    def test_version_mismatch_and_missing_source_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "manifest version"):
            self.check(version="2.0.0b96")
        self.git("update-ref", "-d", "refs/remotes/origin/dev")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.check()

    def test_branch_advance_requires_reviewing_the_new_tip(self):
        self.git("commit", "--allow-empty", "--quiet", "-m", "New candidate")
        self.git("update-ref", "refs/remotes/origin/dev", self.git("rev-parse", "HEAD"))
        with self.assertRaisesRegex(ValueError, "source tip"):
            self.check()

    def test_existing_version_cannot_be_republished(self):
        for tag in ("v2.0.0b95", "2.0.0b95"):
            with self.subTest(tag=tag):
                self.git("tag", tag)
                with self.assertRaisesRegex(ValueError, "already exists"):
                    self.check()
                self.git("tag", "-d", tag)


if __name__ == "__main__":
    unittest.main()
