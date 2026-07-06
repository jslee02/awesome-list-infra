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
ADDED_ENTRY_RE = re.compile(r"^\+\s*-\s+name\s*:\s*(?P<name>.+?)\s*$")
REMOVED_ENTRY_RE = re.compile(r"^-\s*-\s+name\s*:\s*(?P<name>.+?)\s*$")


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
    current_file = ""
    added_entries: list[ResourceAddition] = []
    removed_entry_count = 0

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        header_match = DIFF_HEADER_RE.match(line)
        if header_match:
            current_file = header_match.group("new")
            continue

        if not current_file or not _matches_path(current_file, path_patterns):
            continue

        added_match = ADDED_ENTRY_RE.match(line)
        if added_match:
            added_entries.append(
                ResourceAddition(
                    file=current_file,
                    name=_clean_scalar(added_match.group("name")),
                )
            )
            continue

        if REMOVED_ENTRY_RE.match(line):
            removed_entry_count += 1

    return _result(added_entries, removed_entry_count)


def audit_github_files(files: Iterable[dict], path_patterns: list[str]) -> AuditResult:
    added_entries: list[ResourceAddition] = []
    removed_entry_count = 0

    for file_info in files:
        filename = str(file_info.get("filename") or "")
        if not _matches_path(filename, path_patterns):
            continue

        patch = str(file_info.get("patch") or "")
        for line in patch.splitlines():
            added_match = ADDED_ENTRY_RE.match(line)
            if added_match:
                added_entries.append(
                    ResourceAddition(
                        file=filename,
                        name=_clean_scalar(added_match.group("name")),
                    )
                )
                continue

            if REMOVED_ENTRY_RE.match(line):
                removed_entry_count += 1

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
