"""Tests for awesome_list_infra.triage, using real suggestion issues as fixtures."""

import json
import unittest
from datetime import date
from pathlib import Path

from awesome_list_infra import triage

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 10, 11)
# Categories as a list's README generator registers them: (key, title, scope).
triage.load_categories([
    ("kinematics-dynamics", "Kinematics & Dynamics", ""),
    ("simulators", "Simulators", ""),
    ("control", "Control", ""),
    ("slam", "SLAM & State Estimation", ""),
    ("other-awesome-lists", "Other Awesome Lists", ""),
])


def fixture(number: int) -> triage.Suggestion:
    d = json.loads((FIXTURES / f"issue-{number}.json").read_text(encoding="utf-8"))
    return triage.parse_issue(d["title"], d["body"], date.fromisoformat(d["created_at"]))


def repo(**kw) -> triage.RepoInfo:
    base = dict(full_name="o/r", stars=500, created=date(2024, 1, 1), last_commit=date(2026, 9, 1),
                archived=False, license="MIT", outside=10, about="A library for robots.")
    return triage.RepoInfo(**{**base, **kw})


def ctx(**kw) -> triage.Context:
    base = dict(today=TODAY, listed={}, other_open=[], insider=False, pick=False)
    return triage.Context(**{**base, **kw})


NEW_FORM = """### Resource Name

FusionCore

### URL

https://github.com/manankharwar/fusioncore

### GitHub Repository

manankharwar/fusioncore

### Alternative Repository

_No response_

### Category

SLAM & State Estimation

### Description

ROS 2 localization for outdoor robots fusing IMU, wheel odometry, and GPS in a UKF.

### Why should it be included?

_No response_

### Affiliation

I am an author or maintainer

### Confirmation

- [{box}] I am a person, and I wrote or reviewed this suggestion myself.
"""


class ParseTest(unittest.TestCase):
    def test_old_form(self):
        s = fixture(143)
        self.assertEqual(s.repo, "provael/provael")
        self.assertEqual(s.name, "Provael")
        self.assertTrue(s.legacy)
        self.assertIsNone(triage.category_target(s.category))  # "Dynamics Simulation" was split

    def test_form_without_blank_lines(self):
        s = fixture(150)
        self.assertEqual(s.repo, "shaneraphel/aletheia-reachlock")
        self.assertIsNone(triage.category_target(s.category))  # an old category: a maintainer picks

    def test_free_form_issue(self):
        s = fixture(151)
        self.assertEqual(s.repo, "Promethe-us/awesome-jev")
        self.assertEqual(s.name, "awesome-jev")
        self.assertEqual(triage.category_target(s.category), ("other-awesome-lists", None))

    def test_untitled_issue_uses_form_name(self):
        self.assertEqual(fixture(138).name, "Dimensional")

    def test_not_on_github(self):
        s = fixture(105)
        self.assertIsNone(s.repo)
        self.assertEqual(s.url, "https://prototwin.com")

    def test_new_form_attestation(self):
        checked = triage.parse_issue("[Suggestion] FusionCore", NEW_FORM.format(box="X"), TODAY)
        unchecked = triage.parse_issue("[Suggestion] FusionCore", NEW_FORM.format(box=" "), TODAY)
        self.assertTrue(checked.attested_body)
        self.assertFalse(checked.legacy)
        self.assertEqual(checked.affiliation, "I am an author or maintainer")
        self.assertFalse(unchecked.attested_body)
        replied = triage.parse_issue("x", NEW_FORM.format(box=" "), TODAY,
                                     ["Sure. I am a person, and I wrote or reviewed this suggestion myself."])
        self.assertTrue(replied.attested_reply)

    def test_reply_repos_and_affiliation(self):
        s = triage.parse_issue("x", "### Resource Name\n\nx", TODAY,
                               ["first: https://github.com/o3/r3", "Affiliation: none\n`o2/r2`"])
        self.assertEqual(s.reply_repos, ["o2/r2", "o3/r3"])  # newest reply first
        self.assertEqual(s.affiliation, "none")

    def test_repo_links(self):
        self.assertEqual(triage.repo_in_text("see https://github.com/a/b.git."), "a/b")
        self.assertEqual(triage.repo_in_text("https://www.github.com/a/b"), "a/b")
        self.assertIsNone(triage.repo_in_text("https://gist.github.com/alice/abc https://notgithub.com/o/r"))
        self.assertIsNone(triage.repo_in_text(f"https://github.com/{triage.REPO}/issues/1"))
        self.assertEqual(triage.repo_in_field("`owner/repo`"), "owner/repo")
        self.assertEqual(triage.repo_in_text("github.com/sponsors/x and github.com/c/d"), "c/d")


class GateTest(unittest.TestCase):
    def test_regular_lane(self):
        self.assertEqual(triage.outcome(repo(), TODAY), "pass")
        self.assertEqual(triage.outcome(repo(created=date(2026, 7, 1)), TODAY), "monitor")  # too new
        self.assertEqual(triage.outcome(repo(outside=2), TODAY), "monitor")
        self.assertEqual(triage.outcome(repo(stars=99), TODAY), "monitor")

    def test_fast_lane_skips_age_only(self):
        self.assertEqual(triage.outcome(repo(stars=1000, created=date(2026, 9, 1)), TODAY), "pass")
        self.assertEqual(triage.outcome(repo(stars=5000, created=date(2026, 9, 1), outside=2), TODAY), "monitor")

    def test_floor(self):
        self.assertEqual(triage.outcome(repo(stars=20, outside=1), TODAY), "monitor")
        self.assertEqual(triage.outcome(repo(stars=19, outside=1), TODAY), "below")
        self.assertEqual(triage.outcome(repo(stars=500, outside=0), TODAY), "below")

    def test_basics(self):
        self.assertEqual(triage.outcome(repo(license=None), TODAY), "below")
        self.assertEqual(triage.outcome(repo(last_commit=date(2025, 9, 1)), TODAY), "below")
        self.assertEqual(triage.outcome(repo(license="NOASSERTION"), TODAY), "pass")


class DecideTest(unittest.TestCase):
    attested = triage.parse_issue("[Suggestion] FusionCore", NEW_FORM.format(box="X"), TODAY)

    def test_pass_opens_pr(self):
        d = triage.decide(self.attested, repo(), ctx())
        self.assertEqual((d.state, d.target), ("needs-review", ("slam", None)))

    def test_unknown_category_waits_for_maintainer(self):
        d = triage.decide(fixture(138), repo(), ctx())
        self.assertEqual(d.state, "needs-review")
        self.assertIsNone(d.target)

    def test_unattested_or_agent(self):
        unchecked = triage.parse_issue("[Suggestion] x", NEW_FORM.format(box=" "), TODAY)
        agent = triage.parse_issue("[Suggestion] x 🤖🤖🤖", NEW_FORM.format(box="X"), TODAY)
        self.assertEqual(triage.decide(unchecked, repo(), ctx()).state, "question")
        self.assertEqual(triage.decide(agent, repo(), ctx()).state, "question")
        self.assertEqual(triage.decide(unchecked, repo(), ctx(insider=True)).state, "needs-review")

    def test_one_open_suggestion(self):
        d = triage.decide(self.attested, repo(), ctx(other_open=[140]))
        self.assertTrue(d.close)
        self.assertIn("#140", d.message)
        unchecked = triage.parse_issue("[Suggestion] x", NEW_FORM.format(box=" "), TODAY)
        self.assertTrue(triage.decide(unchecked, repo(), ctx(other_open=[140])).close)  # checked before confirmation

    def test_agent_needs_a_persons_reply(self):
        agent = triage.parse_issue("[Suggestion] x 🤖🤖🤖", NEW_FORM.format(box="X"), TODAY,
                                   ["I am a person, and I wrote or reviewed this suggestion myself."])
        self.assertEqual(triage.decide(agent, repo(), ctx()).state, "needs-review")

    def test_affiliation_required_for_new_issues(self):
        body = "Repo: https://github.com/o/r\n\nI am a person, and I wrote or reviewed this suggestion myself."
        after_policy = date(2026, 11, 1)  # earlier issues are exempt as legacy
        s = triage.parse_issue("Add r", body.replace("I am", "- [x] I am"), after_policy)
        d = triage.decide(s, repo(), ctx())
        self.assertEqual(d.state, "question")
        self.assertIn("Affiliation:", d.message)
        s = triage.parse_issue("Add r", body.replace("I am", "- [x] I am"), after_policy, ["Affiliation: user"])
        self.assertNotEqual(triage.decide(s, repo(), ctx()).state, "question")

    def test_category_is_escaped_in_comments(self):
        s = triage.parse_issue("x", NEW_FORM.format(box="X").replace("SLAM & State Estimation", "x` [@evil](http://e) `"), TODAY)
        d = triage.decide(s, repo(), ctx())
        self.assertNotIn("@evil", d.message)
        self.assertNotIn("](http://e)", d.message)

    def test_missing_or_unknown_repo(self):
        no_repo = triage.parse_issue("[Suggestion] x", "### Resource Name\n\nx\n\n### Category\n\nControl", TODAY)
        self.assertEqual(triage.decide(no_repo, None, ctx()).state, "question")
        self.assertEqual(triage.decide(fixture(105), None, ctx()).state, "question")  # not on GitHub, no evidence
        d = json.loads((FIXTURES / "issue-105.json").read_text(encoding="utf-8"))
        with_evidence = triage.parse_issue(d["title"], d["body"], date.fromisoformat(d["created_at"]),
                                           ["Evidence: used in courses at two universities, see https://e.org/a"])
        self.assertEqual(triage.decide(with_evidence, None, ctx()).state, "needs-review")
        self.assertEqual(triage.decide(self.attested, None, ctx()).state, "question")  # 404

    def test_duplicate_and_archived(self):
        listed = {"o/r": "R (Control)"}
        d = triage.decide(self.attested, repo(), ctx(listed=listed))
        self.assertEqual((d.close, d.labels), (True, {"duplicate"}))
        d = triage.decide(self.attested, repo(), ctx(pending={"o/r": 140}))  # already suggested
        self.assertEqual((d.close, d.labels), (True, {"duplicate"}))
        self.assertIn("#140", d.message)
        self.assertTrue(triage.decide(self.attested, repo(archived=True), ctx()).close)

    def test_monitoring_expires(self):
        young = repo(created=date(2026, 7, 1))
        self.assertEqual(triage.decide(self.attested, young, ctx()).state, "monitoring")
        old = ctx(monitoring_since=date(2025, 10, 1))
        self.assertTrue(triage.decide(self.attested, young, old).close)

    def test_below_floor_closes(self):
        d = triage.decide(self.attested, repo(stars=3, outside=0), ctx())
        self.assertTrue(d.close)
        self.assertIn("❌", d.message)

    def test_maintainer_pick(self):
        d = triage.decide(self.attested, repo(stars=3, outside=0), ctx(pick=True))
        self.assertEqual((d.state, d.target), ("needs-review", ("slam", None)))


class EntryTest(unittest.TestCase):
    def test_description_house_style(self):
        self.assertEqual(triage.pick_description("Fast rigid-body dynamics library.", ""),
                         ("Fast rigid-body dynamics library.", False))
        long = "An agentic operating system " + "for many robots " * 10
        desc, trimmed = triage.pick_description(long, "")
        self.assertTrue(trimmed)
        self.assertLessEqual(len(desc), 100)
        self.assertEqual(triage.pick_description("Too short", "Time-indexed storage for robotics data."),
                         ("Time-indexed storage for robotics data.", False))

    def test_unsafe_urls_are_dropped(self):
        self.assertEqual(triage.safe_url("https://example.com/)![x](https://evil.example/a)"), "")
        self.assertEqual(triage.safe_url("https://a.org/docs?x=1#y"), "https://a.org/docs?x=1#y")
        s = triage.parse_issue("x", NEW_FORM.format(box="X").replace(
            "https://github.com/manankharwar/fusioncore", "https://e.com/)![x](https://evil)"), TODAY)
        entry, _ = triage.build_entry(s, repo(homepage="https://fusion.example/"), None)
        self.assertEqual(entry["url"], "https://fusion.example/")

    def test_entry_fields_and_name_flag(self):
        s = triage.parse_issue("[Suggestion] x", NEW_FORM.format(box="X").replace("FusionCore", "FusionKore"), TODAY)
        entry, flags = triage.build_entry(s, repo(full_name="manankharwar/fusioncore"), None)
        self.assertEqual(entry["github"], "manankharwar/fusioncore")
        self.assertNotIn("url", entry)  # the URL was just the repository
        self.assertTrue(any("differs" in f for f in flags))


if __name__ == "__main__":
    unittest.main()


class ReviewRegressionTest(unittest.TestCase):
    """Cases from code review of the pipeline."""

    def test_graphql_errors_are_not_zero_outside_people(self):
        from unittest import mock
        with mock.patch.object(triage, "api", return_value={"errors": [{"message": "boom"}], "data": None}):
            with self.assertRaises(RuntimeError):
                triage.outside_people("o", "r")

    def test_commit_lookup_failure_is_not_activity(self):
        import urllib.error
        from unittest import mock
        meta = {"full_name": "o/r", "default_branch": "main", "created_at": "2024-01-01T00:00:00Z",
                "pushed_at": "2026-10-01T00:00:00Z", "stargazers_count": 1, "archived": False}

        def fake(code):
            def api(method, path, body=None, token=None):
                if path.endswith("/repos/o/r"):
                    return meta
                if "/commits" in path:
                    raise urllib.error.HTTPError(path, code, "x", {}, None)
                return {"data": {"repository": {c: {"pageInfo": {"hasPreviousPage": False, "startCursor": None},
                                                     "nodes": []} for c in ("issues", "pullRequests")}}}
            return api

        with mock.patch.object(triage, "api", side_effect=fake(500)):
            with self.assertRaises(urllib.error.HTTPError):
                triage.fetch_repo("o/r")
        with mock.patch.object(triage, "api", side_effect=fake(409)):  # empty repository
            self.assertEqual(triage.fetch_repo("o/r").last_commit, date(2024, 1, 1))

    def test_evidence_links_are_not_the_project(self):
        body = ("### Resource Name\n\nSimTool\n\n### URL\n\nhttps://simtool.example\n\n### GitHub Repository\n\n"
                "_No response_\n\n### Evidence of use\n\nTutorial: https://github.com/someone/simtool-tutorial")
        s = triage.parse_issue("[Suggestion] SimTool", body, TODAY,
                               ["Evidence: another one at https://github.com/x/course"])
        self.assertIsNone(s.repo)
        self.assertEqual(s.reply_repos, [])

    def test_older_reopened_issue_cannot_skip_the_queue(self):
        active_20 = {"number": 20, "labels": [{"name": "monitoring"}]}
        reopened_10 = {"number": 10, "labels": []}
        self.assertEqual(triage.blocking_suggestions(10, False, [active_20]), [20])
        self.assertEqual(triage.blocking_suggestions(20, True, [reopened_10]), [])  # no mutual block
        # Two untriaged issues: the older one proceeds, the newer one waits.
        self.assertEqual(triage.blocking_suggestions(20, False, [{"number": 21, "labels": []}]), [])
        self.assertEqual(triage.blocking_suggestions(21, False, [{"number": 20, "labels": []}]), [20])

    def test_incomplete_outside_count_goes_to_a_maintainer(self):
        s = triage.parse_issue("[Suggestion] FusionCore", NEW_FORM.format(box="X"), TODAY)
        d = triage.decide(s, repo(outside=0, outside_complete=False), ctx())
        self.assertEqual((d.state, d.close), ("needs-review", False))

    def test_unlabeled_bracketed_title_without_github_link(self):
        issue = {"labels": [], "user": {"type": "User"}, "title": "[Suggestion] SimTool",
                 "body": "Commercial simulator: https://simtool.example"}
        self.assertTrue(triage.is_suggestion(issue))
        feature_request = {"labels": [], "user": {"type": "User"}, "title": "Add dark mode", "body": "Please."}
        self.assertFalse(triage.is_suggestion(feature_request))

    def test_bracketed_title_without_label(self):
        issue = {"labels": [], "user": {"type": "User"}, "title": "[Suggestion] NewTool",
                 "body": "A planner: https://github.com/o/newtool"}
        self.assertTrue(triage.is_suggestion(issue))

    def test_alternative_repository_is_kept(self):
        s = triage.parse_issue("x", "### Resource Name\n\nTool\n\n### URL\n\nhttps://tool.example\n\n"
                               "### Alternative Repository\n\nhttps://gitlab.com/grp/tool.git", TODAY)
        entry, _ = triage.build_entry(s, None, None)
        self.assertEqual((entry["url"], entry["gitlab"]), ("https://tool.example", "grp/tool"))
        nested = triage.parse_issue("x", "### Resource Name\n\nTool\n\n### Alternative Repository\n\n"
                                    "https://gitlab.com/robotics/planning/tool.git", TODAY)
        self.assertEqual(triage.build_entry(nested, None, None)[0]["gitlab"], "robotics/planning/tool")


class ReviewRound3Test(unittest.TestCase):
    def test_existing_branch_action(self):
        # Runs are serialized per issue, so a branch without an open PR is an orphan.
        self.assertEqual(triage.existing_branch_action([]), "replace")  # a run failed after pushing
        self.assertEqual(triage.existing_branch_action([{"state": "closed"}]), "replace")
        self.assertEqual(triage.existing_branch_action([{"state": "closed"}, {"state": "open"}]), "reuse")

    def test_pagination_finishes_when_every_connection_is_exhausted(self):
        from unittest import mock
        pages = {"issues": 3, "pullRequests": 5}

        def api(method, path, body=None, token=None):
            v = body["variables"]
            repo = {}
            for conn, key in (("issues", "ic"), ("pullRequests", "pc")):
                page = int(v[key] or pages[conn])  # cursor = page number; null = newest
                repo[conn] = {"pageInfo": {"hasPreviousPage": page > 1, "startCursor": str(page - 1)},
                              "nodes": [{"authorAssociation": "OWNER", "author": {"login": "me", "__typename": "User"}}]}
            return {"data": {"repository": repo}}

        with mock.patch.object(triage, "api", side_effect=api):
            self.assertEqual(triage.outside_people("o", "r"), (0, True))

    def test_free_form_suggestion_not_on_github(self):
        s = triage.parse_issue("[Suggestion] SimTool", "Commercial simulator: https://simtool.example.", TODAY,
                               ["Evidence: used in two university courses"])
        self.assertEqual((s.url, s.repo), ("https://simtool.example", None))
        self.assertEqual(triage.decide(s, None, ctx()).state, "needs-review")


class NameTest(unittest.TestCase):
    def test_underscores_survive_cleaning(self):
        self.assertEqual(triage.clean_text("ros2_control `x` [y](z) @me", 60), "ros2_control x y(z) me")  # brackets gone, so no link

    def test_closing_references_are_neutralized(self):
        self.assertEqual(triage.clean_text("FusionCore (fixes #123) for C#", 60), "FusionCore (fixes 123) for C#")

    def test_dry_runs_dispatch_nothing(self):
        from unittest import mock
        pr = {"merged": False, "number": 200, "head": {"ref": "add-entry/issue-160", "repo": {"full_name": triage.REPO}}}
        with mock.patch.object(triage, "api") as api:
            self.assertEqual(triage.dispatch_for_closed_pr(pr, dry_run=True), "#160: would dispatch")
            self.assertEqual(triage.dispatch_each([1, 2], dry_run=True), ["#1: would dispatch", "#2: would dispatch"])
            api.assert_not_called()

    def test_merged_pr_dispatches_nothing(self):
        from unittest import mock
        merged = {"merged": True, "number": 200, "head": {"ref": "add-entry/issue-160", "repo": {"full_name": triage.REPO}}}
        with mock.patch.object(triage, "api") as api:
            self.assertEqual(triage.dispatch_for_closed_pr(merged), "PR: nothing to do")
            api.assert_not_called()


class QueueAndTextTest(unittest.TestCase):
    def test_pending_order_matches_the_queue(self):
        newer_active = {"number": 20, "labels": [{"name": "needs-review"}]}
        self.assertTrue(triage.ranks_ahead(newer_active, 10, False))  # reopened older issue waits
        self.assertFalse(triage.ranks_ahead({"number": 10, "labels": []}, 20, True))

    def test_monitoring_starts_at_the_first_marker(self):
        bot = {"type": "Bot"}
        comments = [
            {"user": bot, "body": "<!-- triage state=monitoring since=2026-01-05 -->\nwatching"},
            {"user": bot, "body": "<!-- triage state=question -->\nrepo gone?"},
            {"user": bot, "body": "<!-- triage state=monitoring since=2026-06-01 -->\nwatching again"},
        ]
        latest, state, since = triage.bot_comment(comments)
        self.assertEqual((state, since), ("monitoring", date(2026, 1, 5)))

    def test_abbreviations_do_not_end_sentences(self):
        self.assertEqual(triage.one_sentence("Library for robotics, e.g. manipulation planning."),
                         "Library for robotics, e.g. manipulation planning")
        self.assertEqual(triage.one_sentence("Bindings, e.g. ROS 2 support. Second sentence."),
                         "Bindings, e.g. ROS 2 support")
        self.assertEqual(triage.one_sentence("Fast QP solver. Supports warm starts."), "Fast QP solver")


class LifecycleTest(unittest.TestCase):
    attested = triage.parse_issue("[Suggestion] FusionCore", NEW_FORM.format(box="X"), TODAY)

    def test_a_standing_decline_closes_the_suggestion(self):
        d = triage.decide(self.attested, repo(), ctx(declined=200, pick=True))  # an older pick loses
        self.assertEqual((d.state, d.close), ("closed", True))
        self.assertIn("#200", d.message)

    def test_only_a_pick_after_the_decline_overrides_it(self):
        closed = {"number": 200, "state": "closed", "merged_at": None, "closed_at": "2026-06-01T00:00:00Z"}
        pick = lambda when: {"event": "labeled", "label": {"name": "maintainer-pick"}, "created_at": when}
        self.assertEqual(triage.standing_decline([closed], [pick("2026-05-01T00:00:00Z")], True), 200)
        self.assertIsNone(triage.standing_decline([closed], [pick("2026-07-01T00:00:00Z")], True))
        self.assertEqual(triage.standing_decline([closed], [pick("2026-07-01T00:00:00Z")], False), 200)  # removed since
        self.assertIsNone(triage.standing_decline([closed, {"number": 201, "state": "open", "merged_at": None,
                                                            "closed_at": None}], [], False))

    def test_free_form_body_with_headings(self):
        s = triage.parse_issue("[Suggestion] Tool", "### Project\n\nA planner: https://github.com/o/tool", TODAY)
        self.assertEqual(s.repo, "o/tool")

    def test_stale_issue_is_not_in_active_triage(self):
        stale = {"number": 10, "labels": [{"name": "question"}, {"name": "stale"}]}
        self.assertFalse(triage.ranks_ahead(stale, 5, False))  # ranks by age, like an untriaged issue
        self.assertTrue(triage.ranks_ahead(stale, 20, False))

    def test_entry_marker_tracks_category_and_content(self):
        entry = {"name": "A", "github": "o/a", "description": "Does a."}
        same = triage.entry_marker(dict(entry), "slam")
        self.assertEqual(triage.entry_marker(entry, "slam"), same)
        self.assertNotEqual(triage.entry_marker(entry, "control"), same)
        self.assertNotEqual(triage.entry_marker({**entry, "description": "Does b."}, "slam"), same)


class InfraPortTest(unittest.TestCase):
    def test_amended_bot_commit_is_not_bot_only(self):
        bot = {"login": "github-actions[bot]"}
        self.assertTrue(triage.bot_only([{"author": bot, "committer": bot}]))
        self.assertFalse(triage.bot_only([{"author": bot, "committer": {"login": "maintainer"}}]))

    def test_repo_from_remote(self):
        self.assertEqual(triage.repo_from_remote("git@github.com:jslee02/awesome-gpgpu.git\n"), "jslee02/awesome-gpgpu")
        self.assertEqual(triage.repo_from_remote("https://github.com/o/r"), "o/r")
        self.assertEqual(triage.repo_from_remote(""), "")

    def test_closing_a_suggestion_strips_state_labels(self):
        import os
        import tempfile
        from unittest import mock
        payload = {"action": "closed", "issue": {"number": 7, "labels": [{"name": "monitoring"}]}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
        env = {"GITHUB_EVENT_NAME": "issues", "GITHUB_EVENT_PATH": f.name}
        live_closed = {"state": "closed", "labels": [{"name": "monitoring"}, {"name": "suggestion"}],
                       "user": {"type": "User"}, "title": "[Suggestion] X", "body": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(triage, "api", return_value=live_closed) as api:
            self.assertEqual(triage.targets_from_event(dry_run=False), [])
            api.assert_called_with("DELETE", f"/repos/{triage.REPO}/issues/7/labels/monitoring")
        live_bug = {"state": "closed", "labels": [{"name": "question"}, {"name": "bug"}],
                    "user": {"type": "User"}, "title": "Broken link", "body": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(triage, "api", return_value=live_bug) as api:
            self.assertEqual(triage.targets_from_event(dry_run=False), [])
            self.assertEqual(api.call_count, 1)  # not a suggestion: labels untouched
        live_open = {"state": "open", "labels": [{"name": "monitoring"}]}  # reopened before this ran
        with mock.patch.dict(os.environ, env), mock.patch.object(triage, "api", return_value=live_open) as api:
            self.assertEqual(triage.targets_from_event(dry_run=False), [7])
            self.assertEqual(api.call_count, 1)  # only the read
        os.unlink(f.name)


class QueueStateTest(unittest.TestCase):
    bot = {"type": "Bot"}

    def test_reopened_since_triage(self):
        comments = [{"user": self.bot, "body": "<!-- triage state=monitoring since=2026-01-01 reopen=2026-05-01T00:00:00Z -->"}]
        self.assertTrue(triage.reopened_since_triage(comments, [{"event": "reopened", "created_at": "2026-06-01T00:00:00Z"}]))
        self.assertFalse(triage.reopened_since_triage(comments, [{"event": "reopened", "created_at": "2026-05-01T00:00:00Z"}]))
        self.assertEqual(triage.bot_comment(comments)[2], date(2026, 1, 1))  # since still parses
        self.assertFalse(triage.in_triage({"monitoring"}, reopened=True))
        self.assertFalse(triage.in_triage({"question", "stale"}, reopened=False))

    def test_peers_lose_labels_left_from_before_a_reopen(self):
        from unittest import mock
        peer = {"number": 10, "labels": [{"name": "needs-review"}, {"name": "suggestion"}]}
        history = {
            f"/repos/{triage.REPO}/issues/10/comments": [{"user": self.bot, "body": "<!-- triage state=needs-review -->",
                                                         "updated_at": "2026-05-01T00:00:00Z"}],
            f"/repos/{triage.REPO}/issues/10/events": [{"event": "reopened", "created_at": "2026-06-01T00:00:00Z"}],
        }
        with mock.patch.object(triage, "paged", side_effect=lambda path: history[path]):
            self.assertEqual([l["name"] for l in triage.normalized(peer)["labels"]], ["suggestion"])
        closing = {f"/repos/{triage.REPO}/issues/10/comments": [{"user": self.bot, "body": "<!-- triage state=closed -->"}],
                   f"/repos/{triage.REPO}/issues/10/events": []}
        with mock.patch.object(triage, "paged", side_effect=lambda path: closing[path]):
            self.assertEqual([l["name"] for l in triage.normalized(peer)["labels"]], ["suggestion"])
        untouched = {"number": 11, "labels": [{"name": "suggestion"}]}
        with mock.patch.object(triage, "paged") as paged:
            self.assertIs(triage.normalized(untouched), untouched)  # no state label: no API calls
            paged.assert_not_called()

    def test_listed_alias_after_transfer_or_rename(self):
        from unittest import mock
        with mock.patch.object(triage, "canonical_repo", return_value="new-owner/tool") as canonical:
            listed = triage.with_aliases({"old-owner/tool": "Tool (Control)", "x/other": "Other"}, "new-owner/tool")
            self.assertEqual(listed["new-owner/tool"], "Tool (Control)")
            canonical.assert_called_once_with("old-owner/tool")  # only slugs sharing owner or name
        with mock.patch.object(triage, "canonical_repo", return_value="org/new-name") as canonical:
            listed = triage.with_aliases({"org/old-name": "Tool (Control)", "x/other": "Other"}, "org/new-name")
            self.assertEqual(listed["org/new-name"], "Tool (Control)")
            canonical.assert_called_once_with("org/old-name")

    def test_reopen_reconciled_by_the_marker(self):
        events = [{"event": "reopened", "created_at": "2026-06-01T00:00:00Z"}]
        stale = [{"user": self.bot, "body": "<!-- triage state=needs-review -->", "updated_at": "2026-07-01T00:00:00Z"}]
        fresh = [{"user": self.bot, "body": "<!-- triage state=needs-review reopen=2026-06-01T00:00:00Z -->",
                  "updated_at": "2026-05-01T00:00:00Z"}]
        self.assertTrue(triage.reopened_since_triage(stale, events))  # never recorded that reopen
        self.assertFalse(triage.reopened_since_triage(fresh, events))

    def test_any_issue_event_triages(self):
        import os
        import tempfile
        from unittest import mock
        payload = {"action": "labeled", "label": {"name": "help wanted"}, "issue": {"number": 7, "labels": []}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
        with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "issues", "GITHUB_EVENT_PATH": f.name}):
            self.assertEqual(triage.targets_from_event(dry_run=True), [7])
        os.unlink(f.name)


class Round5Test(unittest.TestCase):
    def test_names_drop_urls(self):
        s = triage.parse_issue("x", "### Resource Name\n\nTool fixes https://github.com/o/r/issues/9", TODAY)
        self.assertEqual(s.name, "Tool fixes")

    def test_inconclusive_count_only_defers_when_it_could_matter(self):
        attested = triage.parse_issue("[Suggestion] FusionCore", NEW_FORM.format(box="X"), TODAY)
        d = triage.decide(attested, repo(stars=5, outside=0, outside_complete=False), ctx())
        self.assertTrue(d.close)  # below the floor on stars whatever the count
        d = triage.decide(attested, repo(outside=0, outside_complete=False), ctx())
        self.assertEqual(d.state, "needs-review")


class Round7Test(unittest.TestCase):
    def test_github_inside_another_url_is_not_a_repo(self):
        self.assertIsNone(triage.repo_in_text("https://docs.example/github.com/google-deepmind/mujoco"))
        self.assertEqual(triage.repo_in_text("[repo](https://github.com/o/r)"), "o/r")
        self.assertEqual(triage.repo_in_text("Repo: github.com/o/r"), "o/r")

    def test_website_from_an_author_reply(self):
        s = triage.parse_issue("[Suggestion] SimTool", "A commercial simulator.", TODAY,
                               ["It's at https://tool.example\nEvidence: https://paper.example/cites"])
        self.assertEqual(s.url, "https://tool.example")


class Round8Test(unittest.TestCase):
    def test_free_form_evidence_links_are_not_the_project(self):
        body = ("SimTool is a commercial simulator: https://simtool.example\n\n"
                "### Evidence of use\n\nTutorial: https://github.com/someone/simtool-tutorial\n")
        s = triage.parse_issue("[Suggestion] SimTool", body, TODAY)
        self.assertEqual((s.repo, s.url), (None, "https://simtool.example"))

    def test_links_in_backticks_or_quotes(self):
        self.assertEqual(triage.first_link("It's at `https://tool.example/docs`."), "https://tool.example/docs")
        self.assertEqual(triage.first_link('see "https://tool.example"'), "https://tool.example")
