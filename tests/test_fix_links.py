from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from awesome_list_infra.fix_links import apply_fixes, get_redirect_fixes, reverted_fixes


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
    def test_a_url_inside_another_url_is_left_alone(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archived = "https://web.archive.org/web/2020/http://old.example/a"
            (root / "README.md").write_text(f"* [A](http://old.example/a)\n* [B]({archived})\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"),
                             f"* [A](https://new.example/a)\n* [B]({archived})\n")

    def test_an_archived_copy_in_data_does_not_block_the_fix(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            archived = "https://web.archive.org/web/2020/http://old.example/a"
            (root / "data" / "tools.yaml").write_text(f"- name: A\n  url: {archived}\n", encoding="utf-8")
            (root / "README.md").write_text("* [live](http://old.example/a)\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "* [live](https://new.example/a)\n")
            self.assertIn(archived, (root / "data" / "tools.yaml").read_text(encoding="utf-8"))

    def test_reference_definition_without_a_space(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("[ref]:http://old.example/a\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "[ref]:https://new.example/a\n")
            proxied = "https://proxy.example/redirect:http://old.example/a"  # a colon inside another URL
            (root / "README.md").write_text(f"* [A](http://old.example/a) and {proxied}\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"),
                             f"* [A](https://new.example/a) and {proxied}\n")

    def test_a_fix_that_applies_nowhere_is_reported(self):
        import json
        import os
        from unittest import mock
        from awesome_list_infra import fix_links
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".github").mkdir()
            (root / ".github" / "notes.md").write_text("http://old.example/a\n", encoding="utf-8")  # not a fix target
            report = {"error_map": {".github/notes.md": [{"url": "http://old.example/a", "status": {"text": "404"}}]},
                      "suggestion_map": {".github/notes.md": [{"original": "http://old.example/a",
                                                                "suggestion": "https://web.archive.org/x"}]}}
            (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
            cwd = os.getcwd()
            os.chdir(root)
            try:
                with mock.patch("sys.argv", ["fix_links", "report.json"]):
                    fix_links.main()
            finally:
                os.chdir(cwd)
            self.assertIn("http://old.example/a", (root / "lychee" / "unfixed-links.md").read_text(encoding="utf-8"))

    def test_a_url_after_a_comma_inside_a_query_is_left_alone(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proxied = "https://proxy.example/?urls=http://other.example/,http://old.example/a"
            (root / "README.md").write_text(f"* [A](http://old.example/a)\n* <{proxied}>\n", encoding="utf-8")
            apply_fixes({"http://old.example/a": "https://new.example/a"}, root)
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"),
                             f"* [A](https://new.example/a)\n* <{proxied}>\n")

    def test_fixed_in_one_file_still_reported_in_another(self):
        from awesome_list_infra.fix_links import get_unfixable
        report = {"error_map": {"./README.md": [{"url": "http://old.example/a", "status": {"text": "404"}}],
                                ".github/notes.md": [{"url": "http://old.example/a", "status": {"text": "404"}}]}}
        unfixable = get_unfixable(report, {("README.md", "http://old.example/a")})
        self.assertEqual(list(unfixable), [".github/notes.md"])

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


class ConsistencyTest(unittest.TestCase):
    def test_compact_flow_yaml_url_is_left_alone_everywhere(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data").mkdir()
            data = root / "data" / "tools.yaml"
            original = "- {url: http://old.example/a,description: X}\n"
            data.write_text(original, encoding="utf-8")
            readme = root / "README.md"
            readme.write_text("[A](http://old.example/a)\n", encoding="utf-8")
            self.assertEqual(apply_fixes({"http://old.example/a": "https://new.example/a"}, root), {})
            self.assertEqual(data.read_text(encoding="utf-8"), original)
            self.assertEqual(readme.read_text(encoding="utf-8"), "[A](http://old.example/a)\n")

class RegenerationTest(unittest.TestCase):
    def test_fixes_undone_by_the_generator_are_found(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("[CC0](http://old.example/cc0) [A](https://new.example/a)\n", encoding="utf-8")
            applied = {"http://old.example/cc0": "https://new.example/cc0", "http://old.example/a": "https://new.example/a"}
            self.assertEqual(reverted_fixes(applied, root), ["http://old.example/cc0"])


if __name__ == "__main__":
    unittest.main()


class RedirectFixesTest(unittest.TestCase):
    def test_only_respellings_and_repository_renames_are_followed(self):
        # From a real lychee report (2026-10-12), in its current JSON shape.
        moves = {
            "http://wiki.ros.org/urdf": "https://wiki.ros.org/urdf",
            "http://www.resibots.eu/limbo/": "http://resibots.eu/limbo/",
            "https://isaac-sim.github.io/IsaacLab": "https://isaac-sim.github.io/IsaacLab/",
            "https://github.com/haosulab/ManiSkill": "https://github.com/mani-skill/ManiSkill",
            # Moved elsewhere: left for a person.
            "https://github.com/o/r/issues/new?template=x.yml": "https://github.com/login?return_to=x",
            "https://github.com/ros2/ros2/wiki": "https://github.com/ros2/ros2",
            "https://www.artiminds.com/": "https://tpglobalfx.com/",
            "http://www.cimne.com/kratos/": "https://cimne.com/wp-content/uploads/2026/03/kratos.png",
            "http://www.sim.informatik.tu-darmstadt.de/res/sw/mbslib": "https://www.sim.informatik.tu-darmstadt.deres/sw/mbslib",
            "https://klampt.org/": "http://klampt.org/",
        }
        report = {"redirect_map": {"README.md": [
            {"origin": old, "redirects": [{"url": new, "code": 301}]} for old, new in moves.items()]}}
        self.assertEqual(get_redirect_fixes(report), dict(list(moves.items())[:4]))

    def test_older_report_shape(self):
        report = {"redirect_map": {"README.md": [{"url": "http://a.example/x", "status": {
            "redirects": {"redirects": [{"url": "https://www.a.example/x/", "code": 301}]}}}]}}
        self.assertEqual(get_redirect_fixes(report), {"http://a.example/x": "https://www.a.example/x/"})
