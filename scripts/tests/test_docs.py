"""Small stdlib regressions for public-path, anchor and synthetic-preview boundaries."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.check_docs import anchors, image_size, link_errors, references
from scripts.docs_preview import asset, document
from scripts.render_architecture import PALETTES, outputs


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

    def test_picture_sources_include_every_srcset_candidate(self):
        text = (
            '<picture><source media="(prefers-color-scheme: dark)" '
            'srcset="dark.svg 1x, dark-2x.svg 2x"><img src="light.svg"></picture>'
        )
        self.assertEqual(references(text), ["light.svg", "dark.svg", "dark-2x.svg"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "light.svg").touch()
            self.assertEqual(len(link_errors(root, root / "README.md", text)), 2)

    def test_architecture_themes_preserve_labels_and_accessibility(self):
        images = outputs()
        self.assertEqual(len(images), 16)
        for name, source in images.items():
            element = ET.fromstring(source)
            self.assertEqual(element.attrib["role"], "img")
            self.assertEqual(element.attrib["aria-labelledby"], "title desc")
            self.assertNotIn("<script", source)
            self.assertNotIn("http", source.split(">", 1)[1])
            if "-dark" in name:
                light = ET.fromstring(images[name.replace("-dark", "")])
                self.assertEqual(list(light.itertext()), list(element.itertext()))

    def test_diagram_text_has_readable_contrast_on_every_card(self):
        def luminance(color):
            channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
            return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

        for palette in PALETTES.values():
            for background in ("page", "blue", "green", "purple"):
                for foreground in ("text", "muted", "accent"):
                    a, b = sorted((luminance(palette[background]), luminance(palette[foreground])))
                    self.assertGreaterEqual((b + 0.05) / (a + 0.05), 4.5)

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
