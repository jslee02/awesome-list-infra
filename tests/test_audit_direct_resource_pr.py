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

    def test_detects_indented_entry_without_parent_context_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -20,6 +20,9 @@\n",
                "       - name: Existing Planner\n",
                "         github: owner/existing\n",
                "+      - name: CERT-FLOW\n",
                "+        github: Archerkattri/CERT-FLOW\n",
                "+        description: Certified route planning.\n",
                "       - name: Other Planner\n",
            ],
            PATTERNS,
        )

        self.assertTrue(result.direct_resource_pr)
        self.assertEqual(result.additions[0].name, "CERT-FLOW")

    def test_detects_indented_entry_with_blank_line_before_fields(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -20,6 +20,10 @@\n",
                "       - name: Existing Planner\n",
                "         github: owner/existing\n",
                "+      - name: CERT-FLOW\n",
                "+\n",
                "+        github: Archerkattri/CERT-FLOW\n",
                "+        description: Certified route planning.\n",
                "       - name: Other Planner\n",
            ],
            PATTERNS,
        )

        self.assertTrue(result.direct_resource_pr)
        self.assertEqual(result.additions[0].name, "CERT-FLOW")

    def test_detects_name_only_indented_entry_without_parent_context(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -20,6 +20,7 @@\n",
                "       - name: Existing Planner\n",
                "+      - name: CERT-FLOW\n",
            ],
            PATTERNS,
        )

        self.assertTrue(result.direct_resource_pr)
        self.assertEqual(result.additions[0].name, "CERT-FLOW")

    def test_detects_name_only_indented_entry_before_outdent(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -20,6 +20,8 @@\n",
                "       - name: Existing Planner\n",
                "+      - name: CERT-FLOW\n",
                "   - name: Next Section\n",
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

    def test_ignores_section_name_without_content_line_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -1,5 +1,6 @@\n",
                " sections:\n",
                "+  - name: New Section\n",
                "   - name: Existing Section\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_nested_metadata_name_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -20,8 +20,10 @@\n",
                " sections:\n",
                "   - name: Motion Planning\n",
                "     content:\n",
                "       - name: Existing Planner\n",
                "         features:\n",
                "+          - name: New Feature\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_nested_metadata_name_without_content_context_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -20,6 +20,7 @@\n",
                "       - name: Existing Planner\n",
                "         models:\n",
                "+          - name: New Model\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_context_free_nested_metadata_name_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/dynamics-simulation.yaml b/data/dynamics-simulation.yaml\n",
                "@@ -20,6 +20,7 @@\n",
                "          - name: Existing Model\n",
                "+          - name: New Model\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_context_free_top_level_metadata_name_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/dynamics-simulation.yaml b/data/dynamics-simulation.yaml\n",
                "@@ -20,6 +20,7 @@\n",
                "    - name: Existing Model\n",
                "+    - name: New Model\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_context_free_indented_section_name_from_patch(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -20,6 +20,7 @@\n",
                "  - name: Existing Section\n",
                "+  - name: New Section\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_resets_context_between_hunks(self):
        result = audit_patch(
            [
                "diff --git a/data/motion-planning.yaml b/data/motion-planning.yaml\n",
                "@@ -1,5 +1,8 @@\n",
                " sections:\n",
                "   - name: Motion Planning\n",
                "     content:\n",
                "       - name: Existing Planner\n",
                "@@ -80,5 +83,8 @@\n",
                " sections:\n",
                "+  - name: New Section\n",
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

    def test_detects_indented_entry_without_parent_context_from_github_files(self):
        result = audit_github_files(
            [
                {
                    "filename": "data/slam.yaml",
                    "patch": "\n".join(
                        [
                            "@@ -20,6 +20,9 @@",
                            "       - name: Existing SLAM",
                            "         github: owner/existing",
                            "+      - name: New SLAM",
                            "+        github: owner/repo",
                            "+        description: A new SLAM library.",
                            "       - name: Other SLAM",
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

    def test_ignores_nested_metadata_name_from_github_files(self):
        result = audit_github_files(
            [
                {
                    "filename": "data/slam.yaml",
                    "patch": "\n".join(
                        [
                            "@@ -20,8 +20,10 @@",
                            " sections:",
                            "   - name: SLAM",
                            "     content:",
                            "       - name: Existing SLAM",
                            "         features:",
                            "+          - name: New Feature",
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

    def test_ignores_indented_renames_without_parent_context(self):
        result = audit_patch(
            [
                "diff --git a/data/vision.yaml b/data/vision.yaml\n",
                "@@ -20,7 +20,7 @@\n",
                "-      - name: Old Name\n",
                "+      - name: New Name\n",
                "         github: owner/repo\n",
            ],
            PATTERNS,
        )

        self.assertFalse(result.direct_resource_pr)
        self.assertEqual(result.additions, [])

    def test_ignores_name_only_indented_renames_without_parent_context(self):
        result = audit_patch(
            [
                "diff --git a/data/vision.yaml b/data/vision.yaml\n",
                "@@ -20,7 +20,7 @@\n",
                "-      - name: Old Name\n",
                "+      - name: New Name\n",
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
