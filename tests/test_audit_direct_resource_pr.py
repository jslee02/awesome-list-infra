import unittest

from awesome_list_infra.audit_direct_resource_pr import (
    audit_github_files,
    audit_patch,
    parse_path_patterns,
)


PATTERNS = parse_path_patterns("data/*.yaml,data/*.yml,data/**/*.yaml")


class AuditDirectResourcePrTest(unittest.TestCase):
    def test_detects_net_new_data_entry_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -1,3 +1,6 @@\n",
                "+- name: CERT-FLOW\n",
                "+  github: Archerkattri/CERT-FLOW\n",
                "+  description: Certified route planning.\n",
            ],
            PATTERNS,
        )

        self.assertTrue(result.direct_resource_pr)
        self.assertEqual(result.additions[0].name, "CERT-FLOW")
        self.assertEqual(result.additions[0].file, "data/motion-planning.yaml")

    def test_detects_indented_data_entry_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -1,5 +1,9 @@\n",
                " sections:\n",
                "   - name: Motion Planning\n",
                "     content:\n",
                "+      - name: CERT-FLOW\n",
                "+        github: Archerkattri/CERT-FLOW\n",
                "+        description: Certified route planning.\n",
            ],
            PATTERNS,
        )

        self.assertTrue(result.direct_resource_pr)
        self.assertEqual(result.additions[0].name, "CERT-FLOW")

    def test_ignores_indented_section_name_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -1,5 +1,8 @@\n",
                " sections:\n",
                "+  - name: Motion Planning\n",
                "+    content:\n",
                "   - name: Existing Section\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_detects_net_new_data_entry_from_github_files(self):
        result = audit_github_files(
            [
                {
                    "filename": "data/slam.yaml",
                    "patch": "\n".join(
                        [
                            "@@ -1,3 +1,6 @@",
                            "+- name: New SLAM",
                            "+  github: owner/repo",
                        ]
                    ),
                }
            ],
            PATTERNS,
        )

        self.assertTrue(result.direct_resource_pr)
        self.assertEqual(result.additions[0].name, "New SLAM")

    def test_detects_indented_data_entry_from_github_files(self):
        result = audit_github_files(
            [
                {
                    "filename": "data/slam.yaml",
                    "patch": "\n".join(
                        [
                            "@@ -1,5 +1,8 @@",
                            " sections:",
                            "   - name: SLAM",
                            "     content:",
                            "+      - name: New SLAM",
                            "+        github: owner/repo",
                        ]
                    ),
                }
            ],
            PATTERNS,
        )

        self.assertTrue(result.direct_resource_pr)
        self.assertEqual(result.additions[0].name, "New SLAM")

    def test_ignores_indented_section_name_from_github_files(self):
        result = audit_github_files(
            [
                {
                    "filename": "data/slam.yaml",
                    "patch": "\n".join(
                        [
                            "@@ -1,3 +1,6 @@",
                            " sections:",
                            "+  - name: SLAM",
                            "+    content:",
                        ]
                    ),
                }
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_existing_entry_updates(self):
        result = audit_patch(
            [
                "diff --git a/data/slam.yaml b/data/slam.yaml\n",
                "@@ -10,7 +10,7 @@\n",
                " - name: Existing SLAM\n",
                "-  description: Old text.\n",
                "+  description: Better text.\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_renames_without_net_new_entry(self):
        result = audit_patch(
            [
                "diff --git a/data/vision.yaml b/data/vision.yaml\n",
                "@@ -20,7 +20,7 @@\n",
                "-- name: Old Name\n",
                "+- name: New Name\n",
                "   github: owner/repo\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_indented_renames_without_net_new_entry(self):
        result = audit_patch(
            [
                "diff --git a/data/vision.yaml b/data/vision.yaml\n",
                "@@ -20,7 +20,7 @@\n",
                " sections:\n",
                "   - name: Vision\n",
                "     content:\n",
                "-      - name: Old Name\n",
                "+      - name: New Name\n",
                "         github: owner/repo\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_non_data_files(self):
        result = audit_patch(
            [
                "diff --git a/README.md b/README.md\n",
                "@@ -1,3 +1,4 @@\n",
                "+- name: Not YAML\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)


if __name__ == "__main__":
    unittest.main()
