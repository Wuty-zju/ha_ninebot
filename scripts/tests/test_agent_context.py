"""Isolated navigation contracts; no HA, cloud, private reads or native binary."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.agent_context import (
    build_evidence_index,
    check,
    context,
    repository_file,
    resolve_document,
)


class AgentContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ("docs/agent", "docs/evidence", "custom_components/ninebot"):
            (self.root / name).mkdir(parents=True)
        (self.root / "docs/example.md").write_text("Example\n")
        self.write_json(
            "docs/agent/catalog.json",
            {
                "schema_version": 1,
                "documents": [{"path": "docs/example.md"}],
                "topics": {"raw": {"focus": "Review schema", "docs": ["docs/example.md"]}},
            },
        )
        self.write_json(
            "custom_components/ninebot/manifest.json",
            {"version": "2.0.0b24", "requirements": ["ninecli==0.1.7"]},
        )
        self.write_json(
            "docs/evidence/sample.json", {"password": "private-marker", "date": "2026-10-05"}
        )
        self.write_json("docs/agent/evidence-index.json", build_evidence_index(self.root))

    def write_json(self, name: str, data: object) -> None:
        (self.root / name).write_text(json.dumps(data))

    def test_escape_and_external_symlink_rejected(self) -> None:
        with self.assertRaises(ValueError):
            repository_file(self.root, "../outside")
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "secret"
            outside.write_text("secret")
            (self.root / "escape").symlink_to(outside)
            with self.assertRaises(ValueError):
                repository_file(self.root, "escape")

    def test_index_is_metadata_only_and_drift_is_detected(self) -> None:
        encoded = json.dumps(build_evidence_index(self.root))
        self.assertNotIn("private-marker", encoded)
        self.assertNotIn("password", encoded)
        self.assertEqual(check(self.root), [])
        self.write_json("docs/evidence/sample.json", {"date": "2026-10-06"})
        self.assertIn("Evidence index stale: run --refresh-index", check(self.root))

    def test_orphan_document_and_private_link_are_detected(self) -> None:
        (self.root / "docs/orphan.md").write_text("[private](../../outside)\n")
        errors = check(self.root)
        self.assertIn("Unclassified document: docs/orphan.md", errors)
        self.assertTrue(any("Invalid public link:" in error for error in errors))
        catalog_path = self.root / "docs/agent/catalog.json"
        catalog = json.loads(catalog_path.read_text())
        catalog["documents"][0]["overridden_by"] = ["docs/missing.md"]
        catalog_path.write_text(json.dumps(catalog))
        self.assertIn("Invalid override target: docs/missing.md", check(self.root))

    def test_context_does_not_load_evidence_payload(self) -> None:
        output = context(self.root, "raw")
        self.assertIn("docs/example.md", output)
        self.assertNotIn("private-marker", output)
        with self.assertRaises(ValueError):
            context(self.root, "unknown")

    def test_legacy_locator_is_validated_and_never_reads_payload(self) -> None:
        path = self.root / "docs/agent/catalog.json"
        catalog = json.loads(path.read_text())
        catalog["legacy_paths"] = {
            "docs/old.md": {"path": "docs/example.md", "current_contract": "docs/example.md"}
        }
        path.write_text(json.dumps(catalog))
        self.assertEqual(resolve_document(self.root, "docs/old.md")["path"], "docs/example.md")
        self.assertEqual(check(self.root), [])
        with self.assertRaises(ValueError):
            resolve_document(self.root, "../private.md")
        catalog["legacy_paths"]["docs/old.md"]["path"] = "../private.md"
        path.write_text(json.dumps(catalog))
        with self.assertRaises(ValueError):
            resolve_document(self.root, "docs/old.md")
        self.assertTrue(any("Invalid legacy target" in error for error in check(self.root)))


if __name__ == "__main__":
    unittest.main()
