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

    def test_yaml_that_would_break_is_left_alone_everywhere(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            original = "- url: 'http://old.example/a'\n"
            data.write_text(original, encoding="utf-8")
            readme = root / "README.md"
            readme.write_text("[A](http://old.example/a)\n", encoding="utf-8")
            changes = apply_fixes({"http://old.example/a": "https://new.example/O'Reilly"}, root)
            self.assertEqual(changes, {})  # the README is not fixed either, so it matches its data
            self.assertEqual(data.read_text(encoding="utf-8"), original)
            self.assertEqual(readme.read_text(encoding="utf-8"), "[A](http://old.example/a)\n")

    def test_yaml_that_would_change_shape_is_left_alone(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            original = "content: [http://old.example/a]\n"
            data.write_text(original, encoding="utf-8")
            self.assertEqual(apply_fixes({"http://old.example/a": "https://new.example/a,b"}, root), {})
            self.assertEqual(data.read_text(encoding="utf-8"), original)

    def test_only_the_unsafe_url_is_rejected(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            data.write_text("- url: 'http://old.example/a'\n- url: http://old.example/b\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/O'Reilly",
                         "http://old.example/b": "https://new.example/b"}, root)
            self.assertEqual(data.read_text(encoding="utf-8"),
                             "- url: 'http://old.example/a'\n- url: https://new.example/b\n")

    def test_flow_yaml_and_sentence_punctuation_end_a_url(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            data.write_text("- {name: A, url: http://old.example/a, description: X}\n"
                            "- {name: B, url: \"http://old.example/a,b\"}\n", encoding="utf-8")  # quoted: one scalar
            readme = root / "README.md"
            readme.write_text("See http://old.example/a. Also http://old.example/a.html\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual(data.read_text(encoding="utf-8"),
                             "- {name: A, url: https://new.example/a, description: X}\n"
                             "- {name: B, url: \"http://old.example/a,b\"}\n")
            self.assertEqual(readme.read_text(encoding="utf-8"),
                             "See https://new.example/a. Also http://old.example/a.html\n")

    def test_url_followed_by_parenthesis_is_not_a_prefix_match(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            readme = root / "README.md"
            readme.write_text("see http://old.example/a(1) and [A](http://old.example/a)\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual(readme.read_text(encoding="utf-8"),
                             "see http://old.example/a(1) and [A](https://new.example/a)\n")


class ApplyFixesRobustnessTest(unittest.TestCase):
    def test_cyclic_yaml_anchor(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            data.write_text("cycle: &x [*x]\nurl: http://old.example/a\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertIn("url: https://new.example/a", data.read_text(encoding="utf-8"))

    def test_crlf_line_endings_are_kept(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            data.write_bytes(b"- name: A\r\n  url: http://old.example/a\r\n")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual(data.read_bytes(), b"- name: A\r\n  url: https://new.example/a\r\n")


if __name__ == "__main__":
    unittest.main()
