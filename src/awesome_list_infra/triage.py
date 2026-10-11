"""Triage resource suggestions for an awesome list against its acceptance bar.

Run from the list repository's checkout. The list provides its categories through
the SECTIONS registry of its README generator (``(key, title, ...)`` tuples) and its
own generator and validator scripts; the policy is documented in its CONTRIBUTING.md.

Decisions are pure functions (`parse_issue`, `outcome`, `decide`) so they can be
tested on real issue text; `main` gathers GitHub data and applies the decision.
Issue text is untrusted: it only ever flows into YAML, API payloads, or argv
lists, never into a shell command.

    awesome-list-triage                      # CI: reads GITHUB_EVENT_NAME/PATH
    awesome-list-triage --issue 153 --dry-run
    awesome-list-triage --all --dry-run      # every open suggestion
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path.cwd()  # the list repository's checkout
DATA = ROOT / "data"
def checkout_repo() -> str:
    """owner/repo of the checkout: GITHUB_REPOSITORY in CI, else the origin remote."""
    if repo := os.environ.get("GITHUB_REPOSITORY"):
        return repo
    return repo_from_remote(subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                                           capture_output=True, text=True).stdout)


def repo_from_remote(url: str) -> str:
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", url)
    return m[1] if m else ""


REPO = checkout_repo()
API = "https://api.github.com"
DEFAULT_BRANCH = os.environ.get("TRIAGE_DEFAULT_BRANCH", "main")
WORKFLOW_FILE = os.environ.get("TRIAGE_WORKFLOW", "triage.yml")  # the list's caller workflow
GENERATOR = os.environ.get("TRIAGE_GENERATOR", "scripts/generate_readme.py")
VALIDATE_COMMAND = os.environ.get("TRIAGE_VALIDATE_COMMAND", "python3 scripts/validate_entries.py")

# ── Policy (CONTRIBUTING.md#acceptance-bar) ─────────────────────────────────
MIN_STARS, FAST_STARS, FLOOR_STARS = 100, 1000, 20
MIN_OUTSIDE, FLOOR_OUTSIDE = 3, 1
MIN_AGE_DAYS = 183  # 6 months
ACTIVE_DAYS = 365
MONITOR_DAYS = 365
INSIDERS = {"OWNER", "MEMBER", "COLLABORATOR"}
ATTESTATION = "i am a person, and i wrote or reviewed this suggestion myself"
AGENT_TAG = "🤖"
# Issues opened before the form asked for affiliation and confirmation.
POLICY_START = date(2026, 10, 12)

MANAGED_LABELS = {"question", "needs-review", "monitoring", "duplicate", "stale",
                  "accepted", "incubator", "rejected"}
LABELS = {
    "suggestion": ("0e8a16", "Resource suggestion"),
    "question": ("d876e3", "Waiting on the issue author"),
    "needs-review": ("fbca04", "Waiting on a maintainer"),
    "monitoring": ("c5def5", "Below the bar; re-checked monthly for up to 12 months"),
    "duplicate": ("cfd3d7", "Already listed or suggested"),
    "maintainer-pick": ("5319e7", "Maintainer accepts despite the gates"),
}

TITLE_TO_KEY: dict[str, str] = {}
KEY_TO_TITLE: dict[str, str] = {}


def load_categories(sections) -> None:
    """Use the list's categories: (key, title, ...) tuples, as in its README generator."""
    TITLE_TO_KEY.clear()
    KEY_TO_TITLE.clear()
    for key, title, *_ in sections:
        TITLE_TO_KEY[title.lower()] = key
        KEY_TO_TITLE[key] = title


def generator_sections(path: Path):
    """The SECTIONS registry of the list's README generator."""
    spec = importlib.util.spec_from_file_location("list_readme_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SECTIONS
# ── Parsing ─────────────────────────────────────────────────────────────────

# The host must be github.com itself, not gist.github.com or notgithub.com.
GITHUB_URL = re.compile(r"(?<![A-Za-z0-9.-])(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
SLUG = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
NOT_OWNERS = {"orgs", "sponsors", "settings", "features", "topics", "apps", "marketplace", "user-attachments"}
KEY_VALUE = re.compile(r"^[\s*-]*\**(name|url|github|category|description|affiliation)\**\s*:\**\s*(.+?)\s*$", re.I | re.M)


def _slug(owner: str, name: str) -> str | None:
    name = name.rstrip(".").removesuffix(".git")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner) or name in ("", ".", "..")
            or owner.lower() in NOT_OWNERS or f"{owner}/{name}".lower() == REPO.lower()):
        return None
    return f"{owner}/{name}"


def repo_in_text(text: str) -> str | None:
    """First github.com/owner/repo link in free text, skipping this list."""
    for m in GITHUB_URL.finditer(text or ""):
        if slug := _slug(m[1], m[2]):
            return slug
    return None


def repo_in_field(value: str) -> str | None:
    """A form field holding `owner/repo` or a GitHub URL."""
    value = (value or "").strip().strip("`").strip()
    if m := SLUG.match(value):
        return _slug(m[1], m[2])
    return repo_in_text(value)


def form_fields(body: str) -> dict[str, str]:
    """Map lowercased `### Heading` names to their text, as issue forms render them."""
    fields: dict[str, str] = {}
    parts = re.split(r"^###\s+(.+?)\s*$", body or "", flags=re.M)
    for heading, value in zip(parts[1::2], parts[2::2]):
        value = value.strip()
        fields[heading.strip().lower()] = "" if value in ("_No response_", "_None_") else value
    return fields


def first_link(text: str) -> str:
    m = re.search(r"https?://[^\s)>\]]+", text or "")
    return m[0].rstrip(".,;:") if m else ""


def clean_text(text: str, limit: int) -> str:
    """Single line, no Markdown-active characters or mentions, at most `limit` characters.

    Underscores stay: names like ros2_control use them, and intraword underscores are not
    emphasis on GitHub.
    """
    text = re.sub(r"[`*\[\]<>|@]", "", " ".join((text or "").split()))
    text = re.sub(r"#(?=\d)", "", text)  # "fixes #123" in a commit would close an unrelated issue
    return text[:limit].strip()


# Characters safe inside a Markdown link destination; excludes ( ) < > [ ] quotes and spaces.
SAFE_URL = re.compile(r"https?://[A-Za-z0-9.-]+(:\d+)?([/?#][A-Za-z0-9._~%!$&*+,;=:@/?#-]*)?")


def safe_url(url: str) -> str:
    """The URL if it is a plain http(s) URL that cannot break out of a Markdown link, else ""."""
    url = (url or "").strip()
    return url if SAFE_URL.fullmatch(url) else ""


@dataclass
class Suggestion:
    name: str
    repo: str | None
    reply_repos: list[str]  # repos named in the author's replies, newest first
    url: str
    category: str
    description: str
    alt_repo: str
    evidence: str  # evidence of use, needed for projects not on GitHub
    affiliation: str
    attested_body: bool  # checked form box
    attested_reply: bool  # confirmation sentence in an author reply
    agent: bool
    legacy: bool


def parse_issue(title: str, body: str, created: date, author_comments: list[str] = ()) -> Suggestion:
    fields = form_fields(body)
    is_form = "resource name" in fields  # the suggestion form's own first field, not any ### heading
    kv = {k.lower(): v for k, v in KEY_VALUE.findall(body or "")}
    replies = list(reversed(list(author_comments)))
    reply_repos: list[str] = []
    for c in replies:
        c = re.split(r"evidence\s*:", c, maxsplit=1, flags=re.I)[0]  # evidence links are not the project
        for found in [repo_in_text(c), *map(repo_in_field, c.splitlines())]:
            if found and found not in reply_repos:
                reply_repos.append(found)
    affiliation = fields.get("affiliation") or kv.get("affiliation") or next(
        (m[1] for c in replies if (m := re.search(r"affiliation\s*:\s*(.+)", c, re.I))), "")
    evidence = fields.get("evidence of use") or next(
        (m[1] for c in replies if (m := re.search(r"evidence\s*:\s*(.+)", c, re.I | re.S))), "")
    title_name = re.sub(r"^\s*(\[suggestion\]|suggest(ion)?:?|add)\s*", "", title or "", flags=re.I)
    return Suggestion(
        # Names never need URLs; dropping them keeps URL-form closing references out of commits.
        name=clean_text(re.sub(r"\S*://\S*", "", fields.get("resource name") or kv.get("name")
                               or title_name.split("—")[0]), 60),
        repo=(repo_in_field(fields.get("github repository", ""))
              or repo_in_field(kv.get("github", ""))
              or repo_in_text(fields.get("url", "") or kv.get("url", ""))
              # Free-form bodies only: a form's other sections (evidence, why) cite other projects.
              or (None if is_form else repo_in_text(body))
              or next(iter(reply_repos), None)),
        reply_repos=reply_repos,
        url=(fields.get("url") or kv.get("url") or ("" if is_form else first_link(body))).strip(),
        category=(fields.get("category") or kv.get("category") or "").strip(),
        description=fields.get("description") or kv.get("description") or "",
        alt_repo=fields.get("alternative repository", "").strip(),
        evidence=evidence.strip(),
        affiliation=clean_text(affiliation, 60),
        attested_body=f"[x] {ATTESTATION}" in (body or "").lower(),
        attested_reply=any(ATTESTATION in c.lower() for c in replies),
        agent=AGENT_TAG in (title or ""),
        legacy="affiliation" not in fields and created < POLICY_START,
    )


def category_target(category: str) -> tuple[str, str | None] | None:
    """(yaml key, subsection) for a form category, or None if a maintainer must pick."""
    key = TITLE_TO_KEY.get(category.strip().lower())
    return (key, None) if key else None


# ── Gates ───────────────────────────────────────────────────────────────────


@dataclass
class RepoInfo:
    full_name: str
    stars: int
    created: date
    last_commit: date
    archived: bool
    license: str | None  # SPDX id; "NOASSERTION" when a license file is unrecognized
    outside: int  # distinct outside issue/PR authors
    outside_complete: bool = True  # False if history was cut off before reaching the gate
    about: str = ""
    homepage: str = ""
    language: str = ""


def gate_rows(r: RepoInfo, today: date) -> list[tuple[str, str, str, bool]]:
    age = (today - r.created).days
    return [
        ("Repository age", "6 months (none with 1,000 stars)", f"{age // 30} months", age >= MIN_AGE_DAYS or r.stars >= FAST_STARS),
        ("Stars", f"{MIN_STARS}", f"{r.stars:,}", r.stars >= MIN_STARS),
        ("Outside people", f"{MIN_OUTSIDE}", str(r.outside), r.outside >= MIN_OUTSIDE),
        ("Commit within 12 months", "yes", r.last_commit.isoformat(), (today - r.last_commit).days <= ACTIVE_DAYS),
        ("License", "yes", r.license or "none", r.license is not None),
    ]


def outcome(r: RepoInfo, today: date) -> str:
    """'pass', 'monitor', or 'below' for an unarchived, unlisted repository."""
    basics = r.license is not None and (today - r.last_commit).days <= ACTIVE_DAYS
    mature = (today - r.created).days >= MIN_AGE_DAYS
    if basics and r.outside >= MIN_OUTSIDE and (r.stars >= FAST_STARS or (mature and r.stars >= MIN_STARS)):
        return "pass"
    if basics and r.stars >= FLOOR_STARS and r.outside >= FLOOR_OUTSIDE:
        return "monitor"
    return "below"


def gate_table(r: RepoInfo, today: date) -> str:
    rows = ["| Gate | Needed | This project | |", "|---|---|---|---|"]
    rows += [f"| {g} | {need} | {have} | {'✅' if ok else '❌'} |" for g, need, have, ok in gate_rows(r, today)]
    return "\n".join(rows)


# ── Decision ────────────────────────────────────────────────────────────────


@dataclass
class Decision:
    state: str  # question | needs-review | monitoring | closed
    message: str
    labels: set[str] = field(default_factory=set)
    close: bool = False
    target: tuple[str, str | None] | None = None  # set when a PR should be opened


@dataclass
class Context:
    today: date
    listed: dict[str, str]  # lowercased owner/repo -> "Name (Category)"
    other_open: list[int]  # the author's other open suggestions under review
    insider: bool
    pick: bool  # maintainer-pick label
    pending: dict[str, int] = field(default_factory=dict)  # lowercased owner/repo -> older open suggestion
    declined: int | None = None  # entry PR a maintainer closed without merging
    monitoring_since: date | None = None


BAR = "[acceptance bar](https://github.com/{repo}/blob/main/CONTRIBUTING.md#acceptance-bar)"


def decide(s: Suggestion, r: RepoInfo | None, ctx: Context) -> Decision:
    bar = BAR.format(repo=REPO)
    if ctx.declined:  # reopening or editing doesn't undo a maintainer's decision (see standing_decline)
        return Decision("closed", f"A maintainer declined this in #{ctx.declined}; see the reason there. If "
                        "something has changed, ask a maintainer to take another look.", close=True)
    if ctx.other_open and not ctx.insider and not ctx.pick:
        others = ", ".join(f"#{n}" for n in ctx.other_open)
        return Decision("closed", f"Thanks! Please keep one open suggestion at a time; you already have {others} "
                        "open. Feel free to resubmit this one once that is resolved.", close=True)
    if not (s.legacy or ctx.insider or ctx.pick):
        # An agent may have ticked the form box itself, so an agent-tagged issue needs a person's reply.
        confirmed = s.attested_reply or (s.attested_body and not s.agent)
        asks = []
        if not confirmed:
            how = "reply" if s.agent else "check the **Confirmation** box (edit the issue), or reply"
            asks.append(f"Confirm that a person reviewed this suggestion: {how} with\n"
                        "  > I am a person, and I wrote or reviewed this suggestion myself.")
        if not s.affiliation:
            asks.append("State your affiliation with the project: reply with `Affiliation: author`, "
                        "`Affiliation: contributor`, `Affiliation: user`, or `Affiliation: none`.")
        if asks:
            why = "The title carries the automated-agent tag. " if s.agent else ""
            return Decision("question", f"{why}Before I can evaluate this suggestion, please:\n\n"
                            + "\n".join(f"- {a}" for a in asks), {"question"})
    target = category_target(s.category)
    if s.repo is None:
        if s.alt_repo or s.url:
            if ctx.pick and target:
                return Decision("needs-review", "Maintainer pick: opening a PR for review.", {"needs-review"}, target=target)
            if not s.evidence and not ctx.pick:
                return Decision("question", "This resource is not on GitHub, so a maintainer reviews it by hand, "
                                "looking for wide use in robotics. Please add **Evidence of use** (edit the issue), "
                                "or reply starting with `Evidence:`, for example independent users or customers, "
                                "citations, or third-party tutorials.", {"question"})
            return Decision("needs-review", "This resource is not on GitHub, so a maintainer will review it by hand.",
                            {"needs-review"})
        return Decision("question", "I couldn't find a GitHub repository. Please add `owner/repo` to the "
                        "**GitHub Repository** field, or reply with it. For a project not on GitHub, add its "
                        "source or website instead.", {"question"})
    if r is None:
        return Decision("question", f"I couldn't find `github.com/{s.repo}`. Please fix the **GitHub Repository** "
                        "field (edit the issue) or reply with the right `owner/repo`.", {"question"})
    for slug in {s.repo.lower(), r.full_name.lower()}:
        if slug in ctx.listed:
            return Decision("closed", f"Thanks! This is already listed as **{ctx.listed[slug]}**. To change that "
                            "entry, edit its `data/*.yaml` file in a PR.", {"duplicate"}, close=True)
        if slug in ctx.pending:
            return Decision("closed", f"Thanks! This repository is already suggested in #{ctx.pending[slug]}; "
                            "please follow that issue.", {"duplicate"}, close=True)
    if r.archived:
        return Decision("closed", "Thanks! The repository is archived, so it can't be added as a new entry.", close=True)

    table = gate_table(r, ctx.today)
    # An inconclusive outside count goes to a maintainer only if more outside people could
    # change the outcome; a repository failing the floor on stars, license, or activity can't pass.
    if r.outside < MIN_OUTSIDE and not r.outside_complete and not ctx.pick \
            and outcome(replace(r, outside=MIN_OUTSIDE), ctx.today) != "below":
        return Decision("needs-review", f"I couldn't confirm the outside-people gate within the newest issues and "
                        f"PRs, so a maintainer will check it.\n\n{table}", {"needs-review"})
    result = "pass" if ctx.pick else outcome(r, ctx.today)
    if result == "pass":
        if target is None:
            category = clean_text(s.category, 40) or "none"
            return Decision("needs-review", f"Passes the {bar}:\n\n{table}\n\nThe category ({category}) "
                            "needs a maintainer: set the **Category** field in the issue and I'll open the PR.",
                            {"needs-review"})
        intro = "Maintainer pick" if ctx.pick and outcome(r, ctx.today) != "pass" else f"Passes the {bar}"
        return Decision("needs-review", f"{intro}. I'm opening a PR adding it to **{KEY_TO_TITLE[target[0]]}**; "
                        f"a maintainer will check scope and category there.\n\n{table}", {"needs-review"}, target=target)
    eligible = r.created.toordinal() + MIN_AGE_DAYS
    when = date.fromordinal(eligible).isoformat() if eligible > ctx.today.toordinal() else None
    age_note = f" By age alone it qualifies on {when}." if when else ""
    if result == "monitor":
        since = ctx.monitoring_since or ctx.today
        if (ctx.today - since).days > MONITOR_DAYS:
            return Decision("closed", f"Monitored since {since} without reaching the {bar}. Closing for now; "
                            f"you're welcome to resubmit once it passes.\n\n{table}", close=True)
        return Decision("monitoring", f"Not yet over the {bar}, but close enough to watch. I re-check monthly "
                        f"until {since + timedelta(days=MONITOR_DAYS)} and open the PR automatically once it passes."
                        f"{age_note}\n\n{table}", {"monitoring"})
    return Decision("closed", f"Thanks for the suggestion! It doesn't meet the {bar} yet:\n\n{table}\n\n"
                    f"Please resubmit any time once it does; no need to ask first.{age_note}", close=True)


# ── Entries ─────────────────────────────────────────────────────────────────


ABBREVIATIONS = {"e.g.", "i.e.", "etc.", "vs.", "approx.", "incl.", "inc.", "ltd.", "co.", "dr.", "no.", "fig.", "al."}


def one_sentence(text: str) -> str:
    """The first sentence: it ends at . ! or ? followed by an uppercase letter, digit, or the end,
    unless the word ending there is an abbreviation like "e.g."."""
    text = clean_text(text, 1000)
    for m in re.finditer(r"[.!?](?=\s+[A-Z0-9]|\s*$)", text):
        if text[:m.end()].rsplit(" ", 1)[-1].lower() not in ABBREVIATIONS:
            return text[:m.end()].rstrip(".!? ")
    return text.rstrip(".!? ")


def pick_description(submitted: str, about: str) -> tuple[str, bool]:
    """(description, trimmed): the first candidate already in house style, else a trimmed one."""
    for cand in (submitted, about):
        s = one_sentence(cand)
        if 20 <= len(s) <= 99:
            return s + ".", False
    s = one_sentence(submitted) or one_sentence(about)
    if len(s) > 99:
        s = s[:99].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return (s + ".", True) if s else ("", True)


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def build_entry(s: Suggestion, r: RepoInfo | None, subsection: str | None) -> tuple[dict, list[str]]:
    flags = []
    entry: dict = {"name": s.name or (r.full_name.split("/")[1] if r else "")}
    repo_name = r.full_name.split("/")[1] if r else ""
    if r and squash(repo_name) not in squash(entry["name"]) and squash(entry["name"]) not in squash(repo_name):
        flags.append(f"Name `{entry['name']}` differs from the repository name `{repo_name}`; check spelling.")
    url = safe_url(s.url)
    if r and (not url or "github.com" in url):
        url = safe_url(r.homepage)
    if url:
        entry["url"] = url
    if r:
        entry["github"] = r.full_name
    elif alt := safe_url(s.alt_repo):
        host = re.match(r"https?://(gitlab\.com|bitbucket\.org)/([^?#]+?)/?(?:[?#]|$)", alt)
        if host:
            path = host[2].removesuffix(".git")
            if host[1] == "bitbucket.org":  # workspace/repo; GitLab paths may nest subgroups
                path = "/".join(path.split("/")[:2])
            entry["gitlab" if host[1] == "gitlab.com" else "bitbucket"] = path
        else:
            entry["code_url"] = alt
    description, trimmed = pick_description(s.description, r.about if r else "")
    if trimmed:
        flags.append("Description was trimmed or is missing; polish it to one sentence of 100 characters or fewer.")
    if description:
        entry["description"] = description
    if subsection:
        entry["_subsection"] = subsection
    if r:
        meta = {"stars": r.stars, "last_commit": r.last_commit.isoformat()}
        if r.license and r.license != "NOASSERTION":
            meta["license"] = r.license
        if r.language:
            meta["language"] = r.language
        entry["_meta"] = meta
    return entry, flags


def listed_repos() -> dict[str, str]:
    listed = {}
    for key, title in KEY_TO_TITLE.items():
        path = DATA / f"{key}.yaml"
        for e in (yaml.safe_load(path.read_text(encoding="utf-8")) or []) if path.exists() else []:
            if e.get("github"):
                listed[e["github"].lower()] = f"{e['name']} ({title})"
    return listed


def add_entry(entry: dict, key: str) -> None:
    path = DATA / f"{key}.yaml"
    entries = (yaml.safe_load(path.read_text(encoding="utf-8")) or []) if path.exists() else []
    entries = sorted(entries + [entry], key=lambda e: e["name"].lower())
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(entries, f, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)
    subprocess.run([sys.executable, str(ROOT / GENERATOR)], cwd=ROOT, check=True, capture_output=True)


def validate_entries() -> bool:
    return subprocess.run(shlex.split(VALIDATE_COMMAND), cwd=ROOT).returncode == 0


# ── GitHub I/O ──────────────────────────────────────────────────────────────


def api(method: str, path: str, body: dict | None = None, token: str | None = None):
    req = urllib.request.Request(
        path if path.startswith("http") else API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token or os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "awesome-robotics-libraries-triage",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def paged(path: str) -> list:
    sep = "&" if "?" in path else "?"
    items, page = [], 1
    while batch := api("GET", f"{path}{sep}per_page=100&page={page}"):
        items += batch
        if len(batch) < 100:
            break
        page += 1
    return items


OUTSIDE_QUERY = """query($o:String!,$n:String!,$ic:String,$pc:String){repository(owner:$o,name:$n){
 issues(last:100,before:$ic){pageInfo{hasPreviousPage startCursor} nodes{authorAssociation author{login __typename}}}
 pullRequests(last:100,before:$pc){pageInfo{hasPreviousPage startCursor} nodes{authorAssociation author{login __typename}}}}}"""
OUTSIDE_PAGES = 5  # newest 500 issues and 500 PRs at most


def outside_people(owner: str, name: str) -> tuple[int, bool]:
    """(distinct non-insider, non-bot issue/PR authors, whether the count is conclusive).

    Stops once the gate is met; inconclusive when the page cap hits before the history ends.
    """
    people: set[str] = set()
    cursors = {"ic": None, "pc": None}
    done = {"issues": False, "pullRequests": False}
    for _ in range(OUTSIDE_PAGES):
        data = api("POST", "/graphql", {"query": OUTSIDE_QUERY, "variables": {"o": owner, "n": name, **cursors}})
        repo = (data.get("data") or {}).get("repository")
        if data.get("errors") or not repo:  # leave the issue untouched; the next run retries
            raise RuntimeError(f"GraphQL query for {owner}/{name} failed: {data.get('errors')}")
        for conn, cursor in (("issues", "ic"), ("pullRequests", "pc")):
            if done[conn]:  # a null cursor re-reads the newest page; ignore it
                continue
            people |= {
                n["author"]["login"] for n in repo[conn]["nodes"]
                if n["author"] and n["author"]["__typename"] != "Bot"
                and not n["author"]["login"].endswith("[bot]") and n["authorAssociation"] not in INSIDERS
            }
            page = repo[conn]["pageInfo"]
            cursors[cursor] = page["startCursor"] if page["hasPreviousPage"] else None
            done[conn] = not page["hasPreviousPage"]
        if len(people) >= MIN_OUTSIDE or all(done.values()):
            return len(people), True
    return len(people), False


def canonical_repo(slug: str) -> str | None:
    """owner/repo as GitHub names it now (following renames), or None if it doesn't exist."""
    try:
        return api("GET", f"/repos/{slug}")["full_name"]
    except urllib.error.HTTPError as e:
        if e.code in (404, 451):
            return None
        raise


def fetch_repo(slug: str) -> RepoInfo | None:
    try:
        d = api("GET", f"/repos/{slug}")
    except urllib.error.HTTPError as e:
        if e.code in (404, 451):
            return None
        raise
    owner, name = d["full_name"].split("/")
    try:
        branch = urllib.parse.quote(d["default_branch"], safe="")
        commit = api("GET", f"/repos/{d['full_name']}/commits?per_page=1&sha={branch}")
    except urllib.error.HTTPError as e:
        if e.code != 409:  # 409: empty repository, so no default-branch commit
            raise
        commit = None
    last = (commit[0]["commit"]["committer"]["date"] if commit else d["created_at"])[:10]
    return RepoInfo(
        full_name=d["full_name"],
        stars=d["stargazers_count"],
        created=date.fromisoformat(d["created_at"][:10]),
        last_commit=min(date.fromisoformat(last), date.today()),
        archived=d["archived"],
        license=(d.get("license") or {}).get("spdx_id") if d.get("license") else None,
        **dict(zip(("outside", "outside_complete"), outside_people(owner, name))),
        about=d.get("description") or "",
        homepage=d.get("homepage") or "",
        language=d.get("language") or "",
    )


def is_suggestion(issue: dict) -> bool:
    labels = {l["name"] for l in issue["labels"]}
    if "pull_request" in issue or labels & {"bug", "maintenance"} or issue["user"]["type"] == "Bot":
        return False
    body, title = issue.get("body") or "", issue["title"]
    return ("suggestion" in labels or "### Resource Name" in body or bool(re.match(r"\s*\[suggestion\]", title, re.I))
            # Bare "Add ..." titles also need a GitHub link, so feature requests aren't mistaken for suggestions.
            or bool(re.match(r"\s*(suggest|add)\b", title, re.I)) and repo_in_text(body) is not None)


ACTIVE_STATES = {"question", "needs-review", "monitoring"}


def ranks_ahead(issue: dict, number: int, active: bool) -> bool:
    """Whether open suggestion `issue` comes before suggestion `number` in the queue.

    Suggestions already in triage (with a state label) come before untriaged ones, then older
    before newer. A total order means two runs never close each other, and reopening an old
    issue can't push out the suggestion under review.
    """
    names = {l["name"] for l in issue["labels"]}
    other = (0 if names & ACTIVE_STATES and "stale" not in names else 1, issue["number"])
    return issue["number"] != number and other < (0 if active else 1, number)


def last_reopen(events: list[dict]) -> str:
    return max((e["created_at"] for e in events if e["event"] == "reopened"), default="")


def standing_decline(entry_prs: list[dict], events: list[dict], pick: bool) -> int | None:
    """The entry PR a maintainer declined (closed unmerged), unless an entry PR is open or merged,
    or maintainer-pick is on the issue and was applied after the decline. A pick from before the
    decline, or one since removed, doesn't override it."""
    if any(pr["state"] == "open" or pr["merged_at"] for pr in entry_prs) or not entry_prs:
        return None
    latest = max(entry_prs, key=lambda pr: pr["closed_at"] or "")
    picks = [e["created_at"] for e in events
             if e["event"] == "labeled" and (e.get("label") or {}).get("name") == "maintainer-pick"]
    return None if pick and picks and max(picks) > (latest["closed_at"] or "") else latest["number"]


def reopened_since_triage(comments: list[dict], events: list[dict]) -> bool:
    """Whether the issue was reopened after the bot last triaged it.

    Each triage records the latest reopen it saw in its marker, so this holds even when the
    reconciled comment text didn't change.
    """
    previous = bot_comment(comments)[0]
    seen = (m[3] or "") if previous and (m := re.match(MARKER, previous["body"])) else ""
    return bool(previous) and last_reopen(events) > seen


def in_triage(labels: set[str], reopened: bool) -> bool:
    """Active in the queue: a state label that is neither stale nor left from before a reopen."""
    return bool(labels & ACTIVE_STATES) and "stale" not in labels and not reopened


def normalized(issue: dict) -> dict:
    """The issue with the labels the queue should see. Fetches its history only when it carries
    an active state label, which a reopen may have left behind."""
    names = {l["name"] for l in issue["labels"]}
    if not in_triage(names, False):
        return issue
    n = issue["number"]
    comments = paged(f"/repos/{REPO}/issues/{n}/comments")
    # Closed by its own run (labels not yet updated), or reopened since its last triage.
    if bot_comment(comments)[1] == "closed" or reopened_since_triage(comments, paged(f"/repos/{REPO}/issues/{n}/events")):
        return {**issue, "labels": [{"name": name} for name in sorted(names - ACTIVE_STATES)]}
    return issue


def with_aliases(listed: dict[str, str], full_name: str) -> dict[str, str]:
    """Listed repositories, plus `full_name` when a listed slug now resolves to it: a transfer
    (same repository name) or a rename (same owner) the weekly metadata refresh hasn't recorded
    yet. Only slugs sharing the owner or the name are resolved, usually a handful at most."""
    target = full_name.lower()
    owner, name = target.split("/")
    for slug, label in list(listed.items()):
        slug_owner, slug_name = slug.split("/")
        if slug != target and (slug_owner == owner or slug_name == name) \
                and (canonical_repo(slug) or "").lower() == target:
            listed[target] = label
    return listed


def blocking_suggestions(number: int, active: bool, open_suggestions: list[dict]) -> list[int]:
    """The author's other open suggestions ahead of `number` in the queue."""
    return [i["number"] for i in open_suggestions if ranks_ahead(i, number, active)]


MARKER = r"<!-- triage state=(\S+)(?: since=(\S+))?(?: reopen=(\S+))? -->"


def bot_comment(comments: list[dict]) -> tuple[dict | None, str, date | None]:
    """The latest triage comment, its state, and when monitoring first began.

    The start is the earliest monitoring marker in the whole history, so passing through
    another state can't restart the 12-month clock.
    """
    latest, state, first = None, "", None
    for c in comments:
        if c["user"]["type"] == "Bot" and (m := re.match(MARKER, c["body"])):
            latest, state = c, m[1]
            if m[1] == "monitoring" and m[2]:
                first = min(first or date.max, date.fromisoformat(m[2]))
    return latest, state, first


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


LISTED = "listed"  # open_pr result when the repository landed on main meanwhile


def existing_branch_action(prs: list[dict]) -> str:
    """'reuse' an existing entry branch's open PR, else 'replace' the branch.

    Runs that open PRs are serialized per issue (see the workflow's concurrency group), so a
    branch without an open PR was left by a closed PR or by a run that failed before opening one.
    """
    return "reuse" if any(pr["state"] == "open" for pr in prs) else "replace"


def entry_marker(entry: dict, key: str) -> str:
    """Fingerprint of a generated entry and its category, kept in the entry PR's body."""
    digest = hashlib.sha256(json.dumps([key, entry], sort_keys=True).encode()).hexdigest()[:12]
    return f"<!-- entry {digest} -->"


BOT_LOGIN = "github-actions[bot]"


def bot_only(commits: list[dict]) -> bool:
    """Whether the bot both authored and committed every commit. An amended commit keeps the bot
    as author but records the maintainer as committer."""
    return all((c.get(role) or {}).get("login") == BOT_LOGIN for c in commits for role in ("author", "committer"))


def flag_outdated(pr: dict, number: int, key: str, why: str) -> None:
    """Note once on an entry PR that its suggestion changed since it was opened."""
    note = f"<!-- outdated {key} -->"
    if not any(note in c["body"] for c in paged(f"/repos/{REPO}/issues/{pr['number']}/comments")):
        api("POST", f"/repos/{REPO}/issues/{pr['number']}/comments", {"body": f"{note}\n"
            f"#{number} changed after this PR was opened, and {why}. Please compare it with the issue "
            "before merging, or close it."})


DECLINED = "declined"  # open_pr result when a maintainer declined the entry PR meanwhile


def open_pr(number: int, s: Suggestion, r: RepoInfo | None, target: tuple[str, str | None],
            decision: str, events: list[dict], pick: bool) -> str | None:
    """Open the entry PR for an issue, or bring an open one up to date with the suggestion.

    An open PR with only bot commits is regenerated in place; one a maintainer has edited is left
    alone and flagged when the suggestion changed. The branch name comes from the issue number only.
    """
    branch = f"add-entry/issue-{number}"
    entry, flags = build_entry(s, r, target[1])
    marker = entry_marker({k: v for k, v in entry.items() if k != "_meta"}, target[0])
    regenerate = None
    prs = api("GET", f"/repos/{REPO}/pulls?state=all&head={REPO.split('/')[0]}:{branch}")
    if standing_decline(prs, events, pick):  # closed by a maintainer after this run read the PR list
        return DECLINED
    if existing := [pr for pr in prs if pr["state"] == "open"]:
        pr = existing[0]
        if marker in (pr["body"] or ""):
            return pr["html_url"]  # still matches the suggestion
        commits = paged(f"/repos/{REPO}/pulls/{pr['number']}/commits")
        if not bot_only(commits):
            flag_outdated(pr, number, marker, "this PR has maintainer edits, so it was not regenerated")
            return pr["html_url"]
        regenerate = pr
    git("fetch", "origin", DEFAULT_BRANCH)
    git("checkout", "-B", branch, f"origin/{DEFAULT_BRANCH}")
    if entry.get("github") and entry["github"].lower() in with_aliases(listed_repos(), entry["github"]):
        # an older entry PR merged since checkout
        git("checkout", "--detach", f"origin/{DEFAULT_BRANCH}")
        return LISTED
    add_entry(entry, target[0])
    if not validate_entries():
        raise SystemExit(f"#{number}: generated entry fails schema validation")
    git("add", "data", "README.md")
    git("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "--signoff", "-m", f"content: add {entry['name']}\n\nCloses #{number}")
    if regenerate:  # only bot commits on it; the lease fails if anyone pushed since
        git("push", f"--force-with-lease=refs/heads/{branch}:{regenerate['head']['sha']}", "origin", branch)
    elif reuse := push_new_branch(branch):
        git("checkout", "--detach", f"origin/{DEFAULT_BRANCH}")
        return reuse
    git("checkout", "--detach", f"origin/{DEFAULT_BRANCH}")
    body = "\n".join([
        f"Adds **{entry['name']}** to **{KEY_TO_TITLE[target[0]]}** (`data/{target[0]}.yaml`), from #{number}.",
        "", decision, "",
        "**Review:** in scope? Right category? Name and description in house style?",
        *([""] + [f"- ⚠️ {f}" for f in flags] if flags else []),
        "", marker, f"Closes #{number}",
    ])
    title = f"content: add {entry['name']}"
    if regenerate:
        api("PATCH", f"/repos/{REPO}/pulls/{regenerate['number']}", {"title": title, "body": body})
        return regenerate["html_url"]
    pr = {"title": title, "head": branch, "base": DEFAULT_BRANCH, "body": body}
    try:
        return api("POST", f"/repos/{REPO}/pulls", pr, token=os.environ.get("PR_TOKEN"))["html_url"]
    except urllib.error.HTTPError as e:
        if e.code != 422:  # 422: a concurrent run opened it first
            raise
        return api("GET", f"/repos/{REPO}/pulls?state=open&head={REPO.split('/')[0]}:{branch}")[0]["html_url"]


def push_new_branch(branch: str) -> str | None:
    """Push a new entry branch. Returns the open PR's URL if one already owns the branch."""
    try:
        git("push", "origin", branch)  # rejected if the branch already exists
        return None
    except subprocess.CalledProcessError:
        prs = api("GET", f"/repos/{REPO}/pulls?state=all&head={REPO.split('/')[0]}:{branch}")
        if existing_branch_action(prs) == "reuse":
            return next(pr["html_url"] for pr in prs if pr["state"] == "open")
        head = api("GET", f"/repos/{REPO}/commits/{branch}")["sha"]
        # The lease fails if anyone moved the branch since we looked at it.
        git("push", f"--force-with-lease=refs/heads/{branch}:{head}", "origin", branch)
        return None


def remove_label(number: int, name: str) -> None:
    try:
        api("DELETE", f"/repos/{REPO}/issues/{number}/labels/{urllib.parse.quote(name)}")
    except urllib.error.HTTPError as e:
        if e.code != 404:  # already gone
            raise


def ensure_labels() -> None:
    existing = {l["name"] for l in paged(f"/repos/{REPO}/labels")}
    for name, (color, description) in LABELS.items():
        if name not in existing:
            try:
                api("POST", f"/repos/{REPO}/labels", {"name": name, "color": color, "description": description})
            except urllib.error.HTTPError as e:
                if e.code != 422:  # 422: a concurrent run created it first
                    raise


def triage(number: int, dry_run: bool) -> str:
    issue = api("GET", f"/repos/{REPO}/issues/{number}")
    if issue["state"] != "open" or not is_suggestion(issue):
        return f"#{number}: skipped"
    author = issue["user"]["login"]
    comments = paged(f"/repos/{REPO}/issues/{number}/comments")
    s = parse_issue(issue["title"], issue.get("body") or "",
                    date.fromisoformat(issue["created_at"][:10]),
                    [c["body"] for c in comments if c["user"]["login"] == author])
    labels = {l["name"] for l in issue["labels"]}
    previous, previous_state, since = bot_comment(comments)
    # Lifecycle facts come from durable state, not the triggering event: GitHub replaces a
    # pending run when a newer one for the same issue is queued, so event details can be lost.
    entry_prs = api("GET", f"/repos/{REPO}/pulls?state=all&head={REPO.split('/')[0]}:add-entry/issue-{number}")
    # Leftover state labels from before a reopen must not give queue priority, in this run or others.
    events = paged(f"/repos/{REPO}/issues/{number}/events")
    declined = standing_decline(entry_prs, events, "maintainer-pick" in labels)
    active = in_triage(labels, reopened_since_triage(comments, events))
    open_issues = [i for i in paged(f"/repos/{REPO}/issues?state=open") if i["number"] != number and is_suggestion(i)]
    others = blocking_suggestions(number, active, [normalized(i) for i in open_issues if i["user"]["login"] == author])
    r = fetch_repo(s.repo) if s.repo else None
    for reply_repo in s.reply_repos if r is None else []:  # the author corrected the repository
        if r := fetch_repo(reply_repo):
            s.repo = reply_repo
            break
    # Earlier open suggestions of the same repository, so two entry PRs never race to merge.
    pending = {}
    same_repo = {s.repo.lower(), r.full_name.lower()} if r else set()
    for i in open_issues if r else []:
        older = parse_issue(i["title"], i.get("body") or "", POLICY_START)
        slug = older.repo
        if slug and slug.lower() not in same_repo:
            slug = canonical_repo(slug)  # renamed, transferred, or gone
        if not slug:  # missing or wrong in the body; the author may have corrected it in a reply
            replies = [c["body"] for c in paged(f"/repos/{REPO}/issues/{i['number']}/comments")
                       if c["user"]["login"] == i["user"]["login"]]
            older = parse_issue(i["title"], i.get("body") or "", POLICY_START, replies)
            slug = next(filter(None, map(canonical_repo, older.reply_repos)), None)
        if slug and slug.lower() in same_repo and ranks_ahead(normalized(i), number, active):
            pending.setdefault(slug.lower(), i["number"])
    listed = with_aliases(listed_repos(), r.full_name) if r else listed_repos()
    ctx = Context(today=date.today(), listed=listed, pending=pending, declined=declined, other_open=others,
                  insider=issue["author_association"] in INSIDERS, pick="maintainer-pick" in labels,
                  monitoring_since=since)
    d = decide(s, r, ctx)
    summary = f"#{number}: {d.state}" + (f" -> PR in {d.target[0]}" if d.target else "") + f" ({s.repo or 'no repo'})"
    if dry_run:
        return summary
    url = open_pr(number, s, r, d.target, d.message, events, ctx.pick) if d.target else None
    if url == LISTED:
        ctx.listed = with_aliases(listed_repos(), r.full_name)  # the working tree is now at the fresh main
        d = decide(s, r, ctx)
    elif url == DECLINED:
        ctx.declined = standing_decline(
            api("GET", f"/repos/{REPO}/pulls?state=all&head={REPO.split('/')[0]}:add-entry/issue-{number}"),
            events, ctx.pick)
        d = decide(s, r, ctx)
    elif url:
        d.message += f"\n\nPR: {url}"
    if not d.target:  # e.g. edited to an unknown category or a failing repository
        for pr in (pr for pr in entry_prs if pr["state"] == "open"):
            flag_outdated(pr, number, f"{d.state}:{hashlib.sha256(d.message.encode()).hexdigest()[:12]}",
                          f"it is now '{d.state}' without an entry")
    marker = (f"<!-- triage state={d.state}" + (f" since={ctx.monitoring_since or ctx.today}" if d.state == "monitoring" else "")
              + (f" reopen={reopen}" if (reopen := last_reopen(events)) else "") + " -->")
    text = f"{marker}\n{d.message}"
    # Labels first: other runs rank this issue by its labels, so they must not still show an old
    # state once the marker says the issue was reconciled.
    # Touch only the labels triage manages, so labels a maintainer adds mid-run (maintainer-pick) survive.
    if add := sorted((d.labels | {"suggestion"}) - labels):
        api("POST", f"/repos/{REPO}/issues/{number}/labels", {"labels": add})
    remove = (labels & MANAGED_LABELS) - d.labels
    if previous_state == d.state:  # nothing changed: removing 'stale' would restart its clock
        remove.discard("stale")
    for name in sorted(remove):
        remove_label(number, name)
    if previous and previous_state == d.state:
        if previous["body"] != text:
            api("PATCH", f"/repos/{REPO}/issues/comments/{previous['id']}", {"body": text})
    else:
        api("POST", f"/repos/{REPO}/issues/{number}/comments", {"body": text})
    if d.close:
        api("PATCH", f"/repos/{REPO}/issues/{number}", {"state": "closed", "state_reason": "not_planned"})
    return summary


def dispatch_for_closed_pr(pr: dict, dry_run: bool = False) -> str:
    """When a maintainer closes an entry PR without merging, re-triage its issue in a run inside
    the issue's concurrency group; triage reads the decline from the PR itself."""
    m = re.fullmatch(r"add-entry/issue-(\d+)", pr["head"]["ref"])
    if pr["merged"] or not m or pr["head"]["repo"]["full_name"] != REPO:
        return "PR: nothing to do"
    return dispatch_each([int(m[1])], dry_run)[0]


def targets_from_event(dry_run: bool) -> list[int]:
    """Issues to triage in this run. Sweeps and closed entry PRs dispatch per-issue runs instead."""
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()) if os.environ.get("GITHUB_EVENT_PATH") else {}
    if event == "pull_request_target":
        if payload.get("action") == "closed":
            print(dispatch_for_closed_pr(payload["pull_request"], dry_run))
        return []
    if event == "schedule":
        print("\n".join(dispatch_each([i["number"] for i in paged(f"/repos/{REPO}/issues?state=open&labels=monitoring")], dry_run)))
        return []
    if event == "workflow_dispatch":
        number = (payload.get("inputs") or {}).get("issue_number")
        if number:
            return [int(number)]
        print("\n".join(dispatch_each(open_suggestions(), dry_run)))
        return []
    issue = payload.get("issue") or {}
    if event == "issues" and payload.get("action") == "closed":
        live = api("GET", f"/repos/{REPO}/issues/{issue['number']}")  # events can arrive out of order
        if live["state"] == "open":
            return [issue["number"]]  # reopened meanwhile: reconcile it instead
        if not dry_run and is_suggestion(live):
            for name in {l["name"] for l in live["labels"]} & ACTIVE_STATES:
                remove_label(issue["number"], name)
        return []
    # Any other issue event (opened, edited, reopened, labeled) re-triages: GitHub may have
    # replaced a queued run for this issue with this one, so always reconcile the issue as a whole.
    if event == "issues":
        return [issue["number"]]
    # Any author reply: the event's label snapshot may predate the question this reply answers.
    if event == "issue_comment" and "pull_request" not in issue and payload["comment"]["user"]["login"] == issue["user"]["login"]:
        return [issue["number"]]
    return []


def dispatch_each(numbers: list[int], dry_run: bool = False) -> list[str]:
    """Triage each issue in its own run, inside that issue's concurrency group, so a sweep never
    races an event-driven run for the same issue."""
    for number in numbers if not dry_run else []:
        api("POST", f"/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
            {"ref": DEFAULT_BRANCH, "inputs": {"issue_number": str(number)}})
    return [f"#{n}: {'would dispatch' if dry_run else 'dispatched'}" for n in numbers]


def open_suggestions() -> list[int]:
    return [i["number"] for i in paged(f"/repos/{REPO}/issues?state=open") if is_suggestion(i)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--issue", type=int, help="triage one issue")
    parser.add_argument("--all", action="store_true", help="triage every open suggestion")
    parser.add_argument("--dry-run", action="store_true", help="print decisions without changing anything")
    args = parser.parse_args()

    load_categories(generator_sections(ROOT / GENERATOR))
    if not args.dry_run and not os.environ.get("GITHUB_ACTIONS"):
        # Opening PRs switches branches in the working tree; run for real only in CI.
        raise SystemExit("Use --dry-run locally; apply changes with the workflow's manual run.")
    if args.issue or args.all:
        numbers = [args.issue] if args.issue else open_suggestions()
    else:
        numbers = targets_from_event(args.dry_run)
    if not args.dry_run and numbers:
        ensure_labels()
    failed = []
    for number in numbers:
        try:
            print(triage(number, args.dry_run))
        except Exception as e:  # keep going so one bad issue doesn't block a sweep
            failed.append(number)
            print(f"#{number}: ERROR {e!r}", file=sys.stderr)
    if failed:
        raise SystemExit(f"triage failed for {failed}")


if __name__ == "__main__":
    main()
