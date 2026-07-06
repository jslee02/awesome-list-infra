"""Detect pull requests that directly add new resource entries."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable


DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")
ENTRY_RE = re.compile(r"^(?P<indent>\s*)-\s+name\s*:\s*(?P<name>.+?)\s*$")
YAML_KEY_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>[A-Za-z_][\w-]*)\s*:")
RESOURCE_FIELD_KEYS = {
    "_meta",
    "_subsection",
    "archived",
    "bitbucket",
    "code_url",
    "description",
    "features",
    "github",
    "gitlab",
    "languages",
    "license",
    "models",
    "url",
}
SECTION_FIELD_KEYS = {"content", "sections"}


@dataclass(frozen=True)
class ResourceAddition:
    file: str
    name: str


@dataclass(frozen=True)
class AuditResult:
    direct_resource_pr: bool
    reason: str
    additions: list[ResourceAddition]
    removed_entry_count: int


@dataclass(frozen=True)
class _PendingEntry:
    marker: str
    addition: ResourceAddition
    indent: int


def parse_path_patterns(value: str) -> list[str]:
    patterns = [item.strip() for item in value.replace("\n", ",").split(",")]
    return [pattern for pattern in patterns if pattern]


def _matches_path(path: str, patterns: list[str]) -> bool:
    normalized = PurePosixPath(path).as_posix()
    for pattern in patterns:
        if pattern.endswith("/") and normalized.startswith(pattern):
            return True
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value


def _diff_payload(line: str) -> tuple[str, str] | None:
    if not line or line.startswith(("+++", "---", "@@")):
        return None
    if line[0] not in {" ", "+", "-"}:
        return None
    return line[0], line[1:]


def _indent_width(value: str) -> int:
    return len(value) - len(value.lstrip(" "))


def _pop_context_for_line(context_stack: list[tuple[int, str]], payload: str) -> int:
    if not payload.strip():
        return 0

    indent = _indent_width(payload)
    while context_stack and indent <= context_stack[-1][0]:
        context_stack.pop()
    return indent


def _is_resource_entry(indent: int, context_stack: list[tuple[int, str]]) -> bool:
    if indent == 0:
        return True

    return any(key == "content" and indent > context_indent for context_indent, key in context_stack)


def _track_context(context_stack: list[tuple[int, str]], payload: str, indent: int) -> None:
    key_match = YAML_KEY_RE.match(payload)
    if not key_match or key_match.group("key") != "content":
        return

    context_stack.append((indent, "content"))


def _field_key(payload: str) -> str:
    key_match = YAML_KEY_RE.match(payload)
    return key_match.group("key") if key_match else ""


def _update_pending_entries(
    pending_entries: list[_PendingEntry],
    marker: str,
    payload: str,
    indent: int,
    added_entries: list[ResourceAddition],
) -> int:
    if not pending_entries:
        return 0

    key = _field_key(payload)
    removed_entry_count = 0
    remaining_entries: list[_PendingEntry] = []
    current_entry_match = ENTRY_RE.match(payload)

    for pending_entry in pending_entries:
        if indent <= pending_entry.indent:
            if (
                current_entry_match
                and indent == pending_entry.indent
                and marker != pending_entry.marker
            ):
                remaining_entries.append(pending_entry)
            continue

        if key in SECTION_FIELD_KEYS:
            continue

        if key in RESOURCE_FIELD_KEYS and marker in {" ", pending_entry.marker}:
            if pending_entry.marker == "+":
                added_entries.append(pending_entry.addition)
            else:
                removed_entry_count += 1
            continue

        remaining_entries.append(pending_entry)

    pending_entries[:] = remaining_entries
    return removed_entry_count


def _collect_diff_lines(
    lines: Iterable[str],
    path_patterns: list[str],
    filename_from_header: bool,
    initial_file: str = "",
) -> tuple[list[ResourceAddition], int]:
    current_file = initial_file
    context_stack: list[tuple[int, str]] = []
    pending_entries: list[_PendingEntry] = []
    added_entries: list[ResourceAddition] = []
    removed_entry_count = 0

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        header_match = DIFF_HEADER_RE.match(line) if filename_from_header else None
        if header_match:
            current_file = header_match.group("new")
            context_stack = []
            pending_entries = []
            continue

        if not current_file or not _matches_path(current_file, path_patterns):
            continue

        diff_payload = _diff_payload(line)
        if not diff_payload:
            continue

        marker, payload = diff_payload
        indent = _pop_context_for_line(context_stack, payload)
        entry_match = ENTRY_RE.match(payload)
        removed_entry_count += _update_pending_entries(
            pending_entries,
            marker,
            payload,
            indent,
            added_entries,
        )

        if entry_match and _is_resource_entry(indent, context_stack):
            if marker == "+":
                added_entries.append(
                    ResourceAddition(
                        file=current_file,
                        name=_clean_scalar(entry_match.group("name")),
                    )
                )
                continue

            if marker == "-":
                removed_entry_count += 1
                continue

        if entry_match and indent > 0 and marker in {"+", "-"}:
            pending_entries.append(
                _PendingEntry(
                    marker=marker,
                    addition=ResourceAddition(
                        file=current_file,
                        name=_clean_scalar(entry_match.group("name")),
                    ),
                    indent=indent,
                )
            )
            continue

        _track_context(context_stack, payload, indent)

    return added_entries, removed_entry_count


def _result(
    added_entries: list[ResourceAddition],
    removed_entry_count: int,
) -> AuditResult:
    net_new_count = len(added_entries) - removed_entry_count
    direct_resource_pr = net_new_count > 0
    reason = (
        "adds new resource entries to data files"
        if direct_resource_pr
        else "does not add a net-new resource entry to data files"
    )

    return AuditResult(
        direct_resource_pr=direct_resource_pr,
        reason=reason,
        additions=added_entries[:net_new_count] if direct_resource_pr else [],
        removed_entry_count=removed_entry_count,
    )


def audit_patch(lines: Iterable[str], path_patterns: list[str]) -> AuditResult:
    added_entries, removed_entry_count = _collect_diff_lines(
        lines,
        path_patterns,
        filename_from_header=True,
    )
    return _result(added_entries, removed_entry_count)


def audit_github_files(files: Iterable[dict], path_patterns: list[str]) -> AuditResult:
    added_entries: list[ResourceAddition] = []
    removed_entry_count = 0

    for file_info in files:
        filename = str(file_info.get("filename") or "")
        if not _matches_path(filename, path_patterns):
            continue

        patch = str(file_info.get("patch") or "")
        file_added_entries, file_removed_entry_count = _collect_diff_lines(
            patch.splitlines(),
            [filename],
            filename_from_header=False,
            initial_file=filename,
        )
        added_entries.extend(file_added_entries)
        removed_entry_count += file_removed_entry_count

    return _result(added_entries, removed_entry_count)


def _github_output(result: AuditResult) -> str:
    names = ", ".join(addition.name for addition in result.additions)
    files = ", ".join(sorted({addition.file for addition in result.additions}))
    lines = [
        f"direct_resource_pr={str(result.direct_resource_pr).lower()}",
        f"reason={result.reason}",
        f"resource_names={names}",
        f"resource_files={files}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect direct resource additions in pull request changes."
    )
    parser.add_argument(
        "--patch",
        help="Unified diff patch to audit. Reads stdin when omitted.",
    )
    parser.add_argument(
        "--files-json",
        help="JSON file from GitHub's pulls.listFiles response.",
    )
    parser.add_argument(
        "--data-paths",
        default="data/*.yaml,data/*.yml,data/**/*.yaml,data/**/*.yml",
        help="Comma- or newline-separated data file globs/prefixes to audit.",
    )
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="Print GitHub Actions output assignments.",
    )
    args = parser.parse_args(argv)

    path_patterns = parse_path_patterns(args.data_paths)
    if args.files_json:
        with open(args.files_json, encoding="utf-8") as files_json:
            result = audit_github_files(json.load(files_json), path_patterns)
    elif args.patch:
        with open(args.patch, encoding="utf-8") as patch_file:
            result = audit_patch(patch_file, path_patterns)
    else:
        result = audit_patch(sys.stdin, path_patterns)

    if args.github_output:
        print(_github_output(result), end="")
    else:
        print(json.dumps(asdict(result), indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
