"""Download audit contracts with synthetic public metadata, no network."""

import unittest

from scripts.download_counts import package_counts
from scripts.publish_release import releases


def release(identity, count, *, draft=False):
    return {
        "id": identity,
        "tag_name": f"v2.0.{identity}b1",
        "draft": draft,
        "assets": [
            {"name": "SHA256SUMS", "id": identity * 10, "download_count": 999},
            {"name": "ninebot.zip", "id": identity, "download_count": count, "state": "uploaded"},
        ],
    }


class DownloadTests(unittest.TestCase):
    def test_packages_include_previews_and_zero_exclude_drafts_and_checksums(self):
        first = release(1, 0)
        result = package_counts([first, release(2, 4), first, release(3, 99, draft=True)])
        self.assertEqual(result["downloads"], 4)
        self.assertEqual(result["package_release_count"], 2)
        self.assertEqual(result["release_count"], 2)

    def test_invalid_counts_are_errors_not_zero(self):
        for count in (None, True, -1, "12"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                package_counts([release(1, count)])

    def test_complete_pagination_keeps_packages_beyond_the_first_hundred(self):
        class API:
            def __init__(self):
                self.pages = []

            def request(self, path):
                self.pages.append(path)
                return (
                    [release(index, 1) for index in range(1, 101)]
                    if path.endswith("page=1")
                    else [release(101, 7)]
                )

        api = API()
        result = package_counts(releases(api))
        self.assertEqual(result["downloads"], 107)
        self.assertEqual(len(api.pages), 2)
        self.assertIn("per_page=100&page=2", api.pages[-1])


if __name__ == "__main__":
    unittest.main()
