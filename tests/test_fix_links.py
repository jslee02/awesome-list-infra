from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from awesome_list_infra.fix_links import apply_fixes


class ApplyFixesTest(unittest.TestCase):
    def test_fixes_data_files_and_markdown(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            (root / "data" / "tools.yaml").write_text("- name: A\n  url: http://old.example/a\n", encoding="utf-8")
            (root / "README.md").write_text("* [A](http://old.example/a)\n", encoding="utf-8")
            (root / ".github").mkdir()
            (root / ".github" / "notes.md").write_text("http://old.example/a\n", encoding="utf-8")

            changes = apply_fixes({"http://old.example/a": "https://new.example/a"}, root)

            self.assertEqual(sorted(changes), ["README.md", "data/tools.yaml"])
            self.assertIn("https://new.example/a", (root / "data" / "tools.yaml").read_text(encoding="utf-8"))
            self.assertIn("http://old.example/a", (root / ".github" / "notes.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
