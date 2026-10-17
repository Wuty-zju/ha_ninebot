"""Small stdlib regressions for public-path, anchor and synthetic-preview boundaries."""

import tempfile
import unittest
from pathlib import Path

from scripts.check_docs import anchors, image_size, link_errors, references
from scripts.docs_preview import asset, document


class DocumentationChecks(unittest.TestCase):
    def test_heading_aliases_unicode_and_fences(self):
        text = (
            '# Title\n## 中文标题\n## Repeat\n## Repeat\n<a id="old-id"></a>\n```yaml\n# Fake\n```'
        )
        self.assertEqual(anchors(text), {"title", "中文标题", "repeat", "repeat-1", "old-id"})

    def test_nested_badges_html_images_and_reference_definitions(self):
        text = (
            "[![badge](https://example.com/b.svg)](guide.md#start)\n"
            '<img src="demo.png">\n[x]: other.md'
        )
        self.assertEqual(
            references(text),
            ["https://example.com/b.svg", "guide.md#start", "demo.png", "other.md"],
        )

    def test_local_fragments_and_broken_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            page = root / "README.md"
            (root / "guide.md").write_text("## Start\n")
            self.assertEqual(link_errors(root, page, "[ok](guide.md#start)"), [])
            self.assertEqual(len(link_errors(root, page, "[bad](guide.md#missing)")), 1)
            self.assertEqual(len(link_errors(root, page, "[bad](missing.md)")), 1)

    def test_path_escape_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "repo"
            root.mkdir()
            (parent / "private.md").write_text("private")
            (root / "escape.md").symlink_to(parent / "private.md")
            for target in ("../private.md", "escape.md"):
                self.assertIn(
                    "escapes repository", link_errors(root, root / "README.md", f"[x]({target})")[0]
                )

    def test_remote_links_are_not_fetched_and_paths_are_portable(self):
        root = Path.cwd()
        page = root / "README.md"
        self.assertEqual(
            link_errors(root, page, "[remote](https://invalid.example/not-queried)"), []
        )
        self.assertTrue(link_errors(root, page, "private path /Users/example/capture"))
        self.assertTrue(link_errors(root, page, "[local](file:///private/data)"))

    def test_preview_is_synthetic_and_restricts_assets(self):
        self.assertIn(b"language: 'en'", document())
        self.assertIn("合成数据".encode(), document("zh-Hans", "0.8"))
        with self.assertRaises(ValueError):
            document("unreviewed")
        for path in ("/NOTICE", "/ninebot-static/../../../../NOTICE", "/ninebot-static/no-file"):
            with self.assertRaises(ValueError):
                asset(path)
        self.assertEqual(asset("/ninebot-static/ninebot-trip-card.js").name, "ninebot-trip-card.js")

    def test_screenshot_format_is_measured_not_assumed(self):
        jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x02\xd0\x05\x00\x01\x01\x11\x00\xff\xd9"
        self.assertEqual(image_size(jpeg), (".jpg", 1280, 720))
        for malformed in (b"", jpeg[:8], b"not an image"):
            with self.assertRaises(ValueError):
                image_size(malformed)


if __name__ == "__main__":
    unittest.main()
