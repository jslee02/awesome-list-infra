"""Generic README generation for simple awesome-list repositories."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml


SKIP_SECTIONS = {
    "contents",
    "contributing",
    "contribution",
    "contribution guidelines",
    "license",
    "table of contents",
}

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


def repo_slug_from_cwd() -> str:
    return Path.cwd().name


def title_from_repo(repo: str) -> str:
    name = repo.split("/")[-1]
    if name.startswith("awesome-"):
        name = name[len("awesome-") :]
    words = [word.upper() if word in {"gpgpu", "ecs"} else word.capitalize() for word in name.split("-")]
    return "Awesome " + " ".join(words)


def normalize_content_lines(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def is_list_content(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("* ", "- ", "+ ")) or re.match(r"\d+\.\s+", stripped) is not None


def normalize_section(section: dict[str, Any], path: str = "sections") -> list[str]:
    errors: list[str] = []
    title = section.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{path}: missing non-empty title")

    content = section.get("content", [])
    if content is None:
        content = []
    if not isinstance(content, list) or not all(isinstance(line, str) for line in content):
        errors.append(f"{path}.{title or '<unknown>'}: content must be a list of strings")

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
        lines.append(f"{indent}- [{title}](#{github_anchor(title)})")
        children = section.get("sections") or []
        lines.extend(iter_toc(children, depth + 1))
    return lines


def render_section(section: dict[str, Any], level: int = 2) -> list[str]:
    title = section["title"]
    if level == 2:
        lines = [f"## [{title}](#contents)", ""]
    else:
        lines = [f"{'#' * level} {title}", ""]

    content = normalize_content_lines(list(section.get("content") or []))
    if content:
        lines.extend(content)
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

            normalized = heading_title.strip().lower()
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
