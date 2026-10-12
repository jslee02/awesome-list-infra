"""Tests for the generic list generator and metadata refresh in awesome_list_infra.readme."""

import unittest
from datetime import date
from unittest import mock

from awesome_list_infra import readme

CONFIG = {"title": "Awesome X", "repository": "o/awesome-x"}


def data_with(last_commit: str, **extra) -> dict:
    entry = {"name": "Tool", "github": "o/tool", "_meta": {"stars": 1, "last_commit": last_commit}}
    return {"sections": [{"title": "Tools", "content": [entry]}], **extra}


class AsOfTest(unittest.TestCase):
    def test_badges_use_the_refresh_date(self):
        # A year old on the refresh date: green, whatever day the README is regenerated.
        text = readme.render_readme(CONFIG, data_with("2025-10-11", as_of="2026-10-11"))
        self.assertIn("* 🟢 Tool", text)
        with mock.patch.object(readme, "date", wraps=date) as fake:
            fake.today.return_value = date(2027, 1, 1)
            self.assertEqual(readme.render_readme(CONFIG, data_with("2025-10-11", as_of="2026-10-11")), text)

    def test_today_before_the_first_refresh(self):
        self.assertIsNone(readme.as_of(data_with("2025-10-11")))
        self.assertIsNone(readme.as_of(data_with("2025-10-11", as_of="not a date")))


class RefreshTest(unittest.TestCase):
    def test_failed_commit_lookup_keeps_the_known_date(self):
        repo = {"stargazers_count": 2, "default_branch": "main", "pushed_at": "2026-10-01T00:00:00Z"}
        data = data_with("2024-01-02")
        with mock.patch.object(readme, "fetch_json", return_value=repo), \
                mock.patch.object(readme, "fetch_default_branch_commit_date", side_effect=TimeoutError), \
                mock.patch.object(readme.time, "sleep"):
            readme.update_metadata(data)
        # Not pushed_at, which counts pushes to any branch.
        self.assertEqual(data["sections"][0]["content"][0]["_meta"], {"stars": 2, "last_commit": "2024-01-02"})


if __name__ == "__main__":
    unittest.main()
