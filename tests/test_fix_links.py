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

    def test_only_complete_urls_are_replaced(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            data.write_text("- url: http://old.example/a\n- url: http://old.example/abc\n"
                            "- url: 'http://old.example/a'\n", encoding="utf-8")
            (root / "README.md").write_text("[A](http://old.example/a) and http://old.example/a.html\n",
                                            encoding="utf-8")

            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)

            self.assertEqual(data.read_text(encoding="utf-8"),
                             "- url: https://new.example/a\n- url: http://old.example/abc\n"
                             "- url: 'https://new.example/a'\n")
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"),
                             "[A](https://new.example/a) and http://old.example/a.html\n")


class ApplyFixesSafetyTest(unittest.TestCase):
    def test_config_file_is_fixed(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "awesome-list.yaml").write_text("description: see http://old.example/a\n", encoding="utf-8")
            changes = apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual(list(changes), ["awesome-list.yaml"])

    def test_yaml_that_would_break_is_left_alone(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            original = "- url: 'http://old.example/a'\n"
            data.write_text(original, encoding="utf-8")
            changes = apply_fixes({"http://old.example/a": "https://new.example/O'Reilly"}, root)
            self.assertEqual(changes, {})
            self.assertEqual(data.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
