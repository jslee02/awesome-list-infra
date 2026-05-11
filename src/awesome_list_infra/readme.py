"""Generic README generation for simple awesome-list repositories."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import yaml

API_BASE = "https://api.github.com"
RATE_LIMIT_PAUSE_AUTH = 0.5
RATE_LIMIT_PAUSE_NOAUTH = 2
USER_AGENT = "awesome-list-infra-metadata-bot"


SKIP_SECTIONS = {
    "contents",
    "contributing",
    "contribution",
    "contribution guidelines",
    "license",
    "table of contents",
}

ENTRY_STRING_FIELDS = {
    "name",
    "url",
    "description",
    "github",
    "gitlab",
    "bitbucket",
    "code_url",
}

ENTRY_BOOLEAN_FIELDS = {"archived"}
ENTRY_INTEGER_FIELDS = {"_indent"}
META_INTEGER_FIELDS = {"stars"}
META_STRING_FIELDS = {"last_commit", "license", "language"}
META_BOOLEAN_FIELDS = {"archived"}

LICENSE_BLOCKS = {
    "cc0": [
        "[![CC0](https://licensebuttons.net/p/zero/1.0/88x31.png)](http://creativecommons.org/publicdomain/zero/1.0/)",
    ],
    "unlicense": [
        "This list is released into the public domain under the [Unlicense](LICENSE).",
    ],
}


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def github_anchor(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"\s+", "-", value)
    return value.strip("-")


def plain_heading(text: str) -> str:
    value = re.sub(r"\]\([^)]+\)", "]", text)
    return value.replace("[", "").replace("]", "").strip()


def repo_slug_from_cwd() -> str:
    return Path.cwd().name


def title_from_repo(repo: str) -> str:
    name = repo.split("/")[-1]
    if name.startswith("awesome-"):
        name = name[len("awesome-") :]
    words = [word.upper() if word in {"gpgpu", "ecs"} else word.capitalize() for word in name.split("-")]
    return "Awesome " + " ".join(words)


def normalize_content_items(lines: list[Any]) -> list[Any]:
    while lines and isinstance(lines[0], str) and not lines[0].strip():
        lines.pop(0)
    while lines and isinstance(lines[-1], str) and not lines[-1].strip():
        lines.pop()
    return lines


def normalize_content_lines(lines: list[str]) -> list[str]:
    return normalize_content_items(lines)


def is_list_content(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("* ", "- ", "+ ")) or re.match(r"\d+\.\s+", stripped) is not None


def validate_entry(item: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{path}: missing non-empty name")

    for field in ENTRY_STRING_FIELDS:
        if field in item and item[field] is not None and not isinstance(item[field], str):
            errors.append(f"{path}.{field}: must be a string")

    for field in ENTRY_BOOLEAN_FIELDS:
        if field in item and not isinstance(item[field], bool):
            errors.append(f"{path}.{field}: must be a boolean")

    for field in ENTRY_INTEGER_FIELDS:
        if field in item and (not isinstance(item[field], int) or item[field] < 0):
            errors.append(f"{path}.{field}: must be a non-negative integer")

    meta = item.get("_meta")
    if meta is not None:
        if not isinstance(meta, dict):
            errors.append(f"{path}._meta: must be a mapping")
        else:
            for field in META_INTEGER_FIELDS:
                if field in meta and not isinstance(meta[field], int):
                    errors.append(f"{path}._meta.{field}: must be an integer")
            for field in META_STRING_FIELDS:
                if field in meta and meta[field] is not None and not isinstance(meta[field], str):
                    errors.append(f"{path}._meta.{field}: must be a string")
            for field in META_BOOLEAN_FIELDS:
                if field in meta and not isinstance(meta[field], bool):
                    errors.append(f"{path}._meta.{field}: must be a boolean")

    return errors


def normalize_section(section: dict[str, Any], path: str = "sections") -> list[str]:
    errors: list[str] = []
    title = section.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{path}: missing non-empty title")

    content = section.get("content", [])
    if content is None:
        content = []
    if not isinstance(content, list):
        errors.append(f"{path}.{title or '<unknown>'}: content must be a list")
    else:
        for idx, item in enumerate(content):
            item_path = f"{path}.{title or '<unknown>'}.content[{idx}]"
            if isinstance(item, str):
                continue
            if isinstance(item, dict):
                errors.extend(validate_entry(item, item_path))
                continue
            errors.append(f"{item_path}: must be a string or structured entry")

    children = section.get("sections", [])
    if children is None:
        children = []
    if not isinstance(children, list):
        errors.append(f"{path}.{title or '<unknown>'}: sections must be a list")
    else:
        for idx, child in enumerate(children):
            if not isinstance(child, dict):
                errors.append(f"{path}.{title or '<unknown>'}.sections[{idx}] must be an object")
            else:
                errors.extend(normalize_section(child, f"{path}.{title or '<unknown>'}.sections[{idx}]"))
    return errors


def validate(config_path: Path, data_path: Path) -> list[str]:
    errors: list[str] = []
    config = load_yaml(config_path)
    data = load_yaml(data_path)

    if not isinstance(config, dict):
        errors.append(f"{config_path}: must be a mapping")
        config = {}
    if not isinstance(data, dict):
        errors.append(f"{data_path}: must be a mapping")
        data = {}

    title = config.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{config_path}: missing non-empty title")

    description = config.get("description")
    if description is not None and not isinstance(description, str):
        errors.append(f"{config_path}: description must be a string")

    repo = config.get("repository")
    if repo is not None and not isinstance(repo, str):
        errors.append(f"{config_path}: repository must be a string")

    license_name = config.get("license", "cc0")
    if not isinstance(license_name, str):
        errors.append(f"{config_path}: license must be a string")
    elif license_name.lower() not in LICENSE_BLOCKS:
        errors.append(f"{config_path}: unsupported license {license_name!r}")

    sections = data.get("sections")
    if not isinstance(sections, list):
        errors.append(f"{data_path}: sections must be a list")
    else:
        for idx, section in enumerate(sections):
            if not isinstance(section, dict):
                errors.append(f"{data_path}: sections[{idx}] must be an object")
            else:
                errors.extend(normalize_section(section, f"sections[{idx}]"))
    return errors


def iter_toc(sections: list[dict[str, Any]], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for section in sections:
        title = section["title"]
        indent = "  " * depth
        lines.append(f"{indent}* [{title}](#{github_anchor(title)})")
        children = section.get("sections") or []
        lines.extend(iter_toc(children, depth + 1))
    return lines


def format_stars(count: int) -> str:
    if count >= 1000:
        return f"{count / 1000:.1f}k".replace(".0k", "k")
    return str(count)


def activity_emoji(entry: dict[str, Any]) -> str:
    if entry.get("archived") or (entry.get("_meta") or {}).get("archived"):
        return "💀"

    last_commit = (entry.get("_meta") or {}).get("last_commit")
    if not last_commit:
        return ""

    try:
        commit_date = date.fromisoformat(str(last_commit))
    except (TypeError, ValueError):
        return ""

    days_ago = (date.today() - commit_date).days
    if days_ago <= 365:
        return "🟢"
    if days_ago <= 730:
        return "🟡"
    return "🔴"


def code_link(entry: dict[str, Any]) -> tuple[str, str] | None:
    if entry.get("github"):
        return ("github", f"https://github.com/{entry['github']}")
    if entry.get("gitlab"):
        return ("gitlab", f"https://gitlab.com/{entry['gitlab']}")
    if entry.get("bitbucket"):
        return ("bitbucket", f"https://bitbucket.org/{entry['bitbucket']}")
    if entry.get("code_url"):
        return ("code", str(entry["code_url"]))
    return None


def render_entry(entry: dict[str, Any]) -> str:
    indent = "  " * int(entry.get("_indent", 0) or 0)
    parts = [f"{indent}*"]
    emoji = activity_emoji(entry)
    if emoji:
        parts.append(emoji)

    name = entry["name"]
    url = entry.get("url")
    parts.append(f"[{name}]({url})" if url else name)

    description = entry.get("description") or ""
    if description:
        parts.append(f"- {description}")

    link = code_link(entry)
    if link:
        label, repo_url = link
        stars = (entry.get("_meta") or {}).get("stars")
        if label == "github" and stars is not None:
            parts.append(f"[⭐ {format_stars(stars)}]({repo_url})")
        else:
            parts.append(f"[[{label}]({repo_url})]")

    return " ".join(parts)


def render_content_item(item: Any) -> str:
    if isinstance(item, dict):
        return render_entry(item)
    return str(item)


def iter_entries(sections: list[dict[str, Any]]):
    for section in sections:
        for item in section.get("content") or []:
            if isinstance(item, dict):
                yield item
        yield from iter_entries(section.get("sections") or [])


def has_activity_metadata(sections: list[dict[str, Any]]) -> bool:
    return any(activity_emoji(entry) for entry in iter_entries(sections))


def render_section(section: dict[str, Any], level: int = 2) -> list[str]:
    title = section["title"]
    if level == 2:
        lines = [f"## [{title}](#contents)", ""]
    else:
        lines = [f"{'#' * level} {title}", ""]

    content = normalize_content_items(list(section.get("content") or []))
    if content:
        lines.extend(render_content_item(item) for item in content)
        lines.append("")

    for child in section.get("sections") or []:
        lines.extend(render_section(child, min(level + 1, 6)))
    return lines


def render_readme(config: dict[str, Any], data: dict[str, Any]) -> str:
    title = config["title"]
    description = config.get("description") or ""
    repository = config.get("repository") or f"jslee02/{repo_slug_from_cwd()}"
    license_name = (config.get("license") or "cc0").lower()
    sections = data.get("sections") or []

    lines: list[str] = [
        f"# {title}",
        "",
        "[![Awesome](https://awesome.re/badge.svg)](https://awesome.re)",
        "",
    ]

    if description:
        lines.extend([description, ""])

    if sections:
        lines.extend(["## Contents", ""])
        lines.extend(iter_toc(sections))
        lines.append("")
        if has_activity_metadata(sections):
            lines.extend(
                [
                    "> **Legend**: 🟢 Active (<1yr) · 🟡 Slow (1-2yr) · 🔴 Stale (>2yr) · 💀 Archived",
                    "",
                ]
            )

    for section in sections:
        lines.extend(render_section(section))

    lines.extend(
        [
            "## [Contributing](#contents)",
            "",
            "Contributions are very welcome. Please read the [contribution guidelines](CONTRIBUTING.md) first. Also, please feel free to report any error.",
            "",
            "## [License](#contents)",
            "",
        ]
    )
    lines.extend(LICENSE_BLOCKS[license_name])
    lines.append("")
    return "\n".join(lines)


def parse_heading(line: str, next_line: str | None = None) -> tuple[int, str] | None:
    atx = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
    if atx:
        return len(atx.group(1)), atx.group(2).strip()

    if next_line is not None:
        if not line.strip() or line.lstrip().startswith(("*", "-", ">", "`")):
            return None
        marker = next_line.strip()
        if len(marker) >= 3 and set(marker) <= {"="}:
            return 1, line.strip()
        if len(marker) >= 3 and set(marker) <= {"-"}:
            return 2, line.strip()
    return None


def section_node(title: str) -> dict[str, Any]:
    return {"title": title, "content": [], "sections": []}


def append_content(stack: list[tuple[int, dict[str, Any]]], root_content: list[str], line: str) -> None:
    if stack:
        stack[-1][1].setdefault("content", []).append(line)
    else:
        root_content.append(line)


def import_readme(readme_path: Path, repository: str) -> tuple[dict[str, Any], dict[str, Any]]:
    lines = readme_path.read_text(encoding="utf-8").splitlines()
    title = title_from_repo(repository)
    root_content: list[str] = []
    sections: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        next_line = lines[idx + 1] if idx + 1 < len(lines) else None
        heading = parse_heading(line, next_line)
        consumed_setext = False

        if heading:
            level, heading_title = heading
            consumed_setext = bool(next_line and not line.lstrip().startswith("#") and set(next_line.strip()) <= {"=", "-"})

            if level == 1 and not sections and not stack:
                title = title_from_repo(repository) if heading_title.startswith("awesome-") else heading_title
                idx += 2 if consumed_setext else 1
                continue

            normalized = plain_heading(heading_title).lower()
            if normalized in SKIP_SECTIONS:
                skip_level = level
                idx += 2 if consumed_setext else 1
                while idx < len(lines):
                    candidate = parse_heading(lines[idx], lines[idx + 1] if idx + 1 < len(lines) else None)
                    if candidate and candidate[0] <= skip_level:
                        break
                    idx += 1
                continue

            node = section_node(heading_title)
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].setdefault("sections", []).append(node)
            else:
                sections.append(node)
            stack.append((level, node))
            idx += 2 if consumed_setext else 1
            continue

        if re.search(r"awesome\.re/badge|rawgit\.com/sindresorhus/awesome|cdn\.rawgit\.com/sindresorhus/awesome", line):
            idx += 1
            continue
        append_content(stack, root_content, line)
        idx += 1

    description_lines = normalize_content_lines(root_content)
    description = ""
    if description_lines:
        paragraph: list[str] = []
        content_start = 0
        if not is_list_content(description_lines[0]):
            for idx, line in enumerate(description_lines):
                if not line.strip():
                    if paragraph:
                        content_start = idx + 1
                        break
                    continue
                if is_list_content(line):
                    content_start = idx
                    break
                paragraph.append(line.strip())
            else:
                content_start = len(description_lines)
        resource_lines = normalize_content_lines(description_lines[content_start:])
        if resource_lines:
            resource_section = {"title": "Resources", "content": resource_lines, "sections": []}
            if sections:
                sections.insert(0, resource_section)
            else:
                sections.append(resource_section)
        description = " ".join(paragraph)

    return (
        {
            "title": title,
            "description": description or f"A curated list of resources for {title.removeprefix('Awesome ')}.",
            "repository": repository,
        },
        {"sections": sections},
    )


def generate(config_path: Path, data_path: Path, output_path: Path) -> None:
    errors = validate(config_path, data_path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    content = render_readme(load_yaml(config_path), load_yaml(data_path))
    output_path.write_text(content, encoding="utf-8")
    print(f"Wrote {output_path}")


def generate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate README.md from generic awesome-list YAML")
    parser.add_argument("-c", "--config", default="awesome-list.yaml")
    parser.add_argument("-d", "--data", default="data/readme.yaml")
    parser.add_argument("-o", "--output", default="README.md")
    args = parser.parse_args(argv)
    generate(Path(args.config), Path(args.data), Path(args.output))
    return 0


def github_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_json(url: str, token: str | None = None) -> Any:
    req = urllib.request.Request(url, headers=github_headers(token))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_default_branch_commit_date(owner_repo: str, default_branch: str, token: str | None = None) -> str | None:
    branch_ref = urllib.parse.quote(default_branch, safe="")
    data = fetch_json(f"{API_BASE}/repos/{owner_repo}/commits?per_page=1&sha={branch_ref}", token)
    if not isinstance(data, list) or not data:
        return None
    commit = data[0].get("commit", {})
    committer = commit.get("committer") or {}
    author = commit.get("author") or {}
    commit_date = (committer.get("date") or author.get("date") or "")[:10]
    return commit_date or None


def fetch_repo_metadata(owner_repo: str) -> dict[str, Any] | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        data = fetch_json(f"{API_BASE}/repos/{owner_repo}", token)
    except urllib.error.HTTPError as e:
        print(f"  WARN: {owner_repo} — HTTP {e.code}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  WARN: {owner_repo} — {e}", file=sys.stderr)
        return None

    meta: dict[str, Any] = {}
    if "stargazers_count" in data:
        meta["stars"] = data["stargazers_count"]

    last_commit = None
    default_branch = data.get("default_branch") or ""
    if default_branch:
        try:
            last_commit = fetch_default_branch_commit_date(owner_repo, default_branch, token)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            pass
    if not last_commit and data.get("pushed_at"):
        last_commit = data["pushed_at"][:10]
    if last_commit:
        meta["last_commit"] = last_commit

    if data.get("archived") is True:
        meta["archived"] = True
    if data.get("license"):
        license_data = data["license"]
        spdx = license_data.get("spdx_id")
        if spdx and spdx != "NOASSERTION":
            meta["license"] = spdx
        elif license_data.get("name"):
            meta["license"] = license_data["name"]
    if data.get("language"):
        meta["language"] = data["language"]
    return meta


def update_metadata(data: dict[str, Any], dry_run: bool = False) -> tuple[int, int]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    total = 0
    updated = 0
    for entry in iter_entries(data.get("sections") or []):
        github = entry.get("github")
        if not github:
            continue
        total += 1
        print(f"  {github}...", end=" ", flush=True)
        meta = fetch_repo_metadata(github)
        if meta:
            if not dry_run:
                entry["_meta"] = meta
            updated += 1
            print(f"★ {meta.get('stars', '?')}")
        else:
            print("skipped")
        time.sleep(RATE_LIMIT_PAUSE_AUTH if token else RATE_LIMIT_PAUSE_NOAUTH)
    return total, updated


def fetch_metadata_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch GitHub metadata for generic awesome-list entries")
    parser.add_argument("-d", "--data", default="data/readme.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("WARNING: No GITHUB_TOKEN set — GitHub rate limit is low", file=sys.stderr)

    data_path = Path(args.data)
    data = load_yaml(data_path)
    if not isinstance(data, dict):
        print(f"ERROR: {data_path}: must be a mapping", file=sys.stderr)
        return 1

    total, updated = update_metadata(data, dry_run=args.dry_run)
    if not args.dry_run and updated:
        write_yaml(data_path, data)
    print(f"Done: {updated}/{total} entries updated")
    if args.dry_run:
        print("(dry run — no files written)")
    return 0


def validate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generic awesome-list YAML")
    parser.add_argument("-c", "--config", default="awesome-list.yaml")
    parser.add_argument("-d", "--data", default="data/readme.yaml")
    args = parser.parse_args(argv)
    errors = validate(Path(args.config), Path(args.data))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Generic awesome-list data validated successfully")
    return 0


def import_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an existing Markdown README into generic awesome-list YAML")
    parser.add_argument("--readme", default="README.md")
    parser.add_argument("--repository", default=f"jslee02/{repo_slug_from_cwd()}")
    parser.add_argument("--config", default="awesome-list.yaml")
    parser.add_argument("--data", default="data/readme.yaml")
    args = parser.parse_args(argv)
    config, data = import_readme(Path(args.readme), args.repository)
    write_yaml(Path(args.config), config)
    write_yaml(Path(args.data), data)
    return 0
