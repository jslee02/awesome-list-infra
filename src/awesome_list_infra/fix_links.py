"""Parse lychee JSON reports and fix broken links in markdown files."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def load_report(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_redirect_fixes(report: dict) -> dict[str, str]:
    fixes: dict[str, str] = {}
    for _source, entries in report.get("redirect_map", {}).items():
        for entry in entries:
            original = entry["url"]
            chain = entry.get("status", {}).get("redirects", {}).get("redirects", [])
            if chain:
                final = chain[-1]["url"]
                if final != original:
                    fixes[original] = final
    return fixes


def get_wayback_fixes(report: dict) -> dict[str, str]:
    fixes: dict[str, str] = {}
    for _source, suggestions in report.get("suggestion_map", {}).items():
        for item in suggestions:
            original = item.get("original", "")
            suggestion = item.get("suggestion", "")
            if original and suggestion:
                fixes[original] = suggestion
    return fixes


def get_unfixable(report: dict, fixed_urls: set[str]) -> dict[str, list[dict]]:
    unfixable: dict[str, list[dict]] = {}
    for source, entries in report.get("error_map", {}).items():
        for entry in entries:
            url = entry["url"]
            if url not in fixed_urls:
                unfixable.setdefault(source, []).append(
                    {
                        "url": url,
                        "status": entry.get("status", {}).get("text", "Unknown"),
                    }
                )
    return unfixable


# A match must end where the URL ends, so fixing ".../a" leaves ".../abc" and
# ".../a(1)" alone. ")" and "'" can't be listed: they close Markdown links and
# single-quoted YAML values.
URL_CONTINUES = r"(?![A-Za-z0-9_\-.~%!$&*+,;=:@/?#(])"


def build_replacement_pattern(fixes: dict[str, str]) -> re.Pattern[str]:
    sorted_urls = sorted(fixes.keys(), key=len, reverse=True)
    return re.compile("(?:" + "|".join(re.escape(url) for url in sorted_urls) + ")" + URL_CONTINUES)


def fix_targets(root: Path) -> list[Path]:
    """Markdown plus the data and config files a generated README is built from."""
    sources = [f for glob in ("data/**/*.yaml", "data/**/*.yml", "awesome-list.yaml") for f in root.glob(glob)]
    return [
        f for f in sorted(set(root.rglob("*.md")) | set(sources))
        if not any(part.startswith(".") or part == "lychee" for part in f.relative_to(root).parts)
    ]


def only_strings_changed(old_text: str, new_text: str, fix) -> bool:
    """Whether new_text parses to the same YAML tree with each string passed through `fix`.

    Catches replacements that break quoting or reshape the document (a URL with a
    comma inside a flow sequence, for example).
    """
    import yaml  # imported here so Markdown-only lists don't need PyYAML

    try:
        old, new = yaml.safe_load(old_text), yaml.safe_load(new_text)
    except yaml.YAMLError:
        return False

    def same(a, b) -> bool:
        if isinstance(a, dict):
            return isinstance(b, dict) and list(a) == list(b) and all(same(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return isinstance(b, list) and len(a) == len(b) and all(map(same, a, b))
        if isinstance(a, str):
            return b == fix(a)
        return a == b

    return same(old, new)


def unsafe_data_fixes(fixes: dict[str, str], root: Path) -> set[str]:
    """URLs whose replacement would corrupt a YAML file, so they're left unfixed everywhere.

    Dropping them from the Markdown too keeps a generated README in sync with its data.
    """
    def corrupts(content: str, subset: dict[str, str]) -> bool:
        pattern = build_replacement_pattern(subset)

        def fix(text: str) -> str:
            return pattern.sub(lambda m: subset[m.group(0)], text)

        new_content = fix(content)
        return new_content != content and not only_strings_changed(content, new_content, fix)

    rejected: set[str] = set()
    for path in fix_targets(root):
        if path.suffix not in (".yaml", ".yml"):
            continue
        content = path.read_text(encoding="utf-8")
        if not corrupts(content, fixes):
            continue
        # Reject only the URLs that are unsafe on their own; if the rest still
        # interact badly, reject everything this file would have changed.
        in_file = set(build_replacement_pattern(fixes).findall(content))
        bad = {url for url in in_file if corrupts(content, {url: fixes[url]})}
        rest = {url: fixes[url] for url in in_file - bad}
        if rest and corrupts(content, rest):
            bad = in_file
        print(f"  not fixing {sorted(bad)} in {path}: the replacement would corrupt the YAML", file=sys.stderr)
        rejected |= bad
    return rejected


def apply_fixes(
    fixes: dict[str, str],
    root: Path,
) -> dict[str, list[tuple[str, str]]]:
    rejected = unsafe_data_fixes(fixes, root)
    fixes = {url: new for url, new in fixes.items() if url not in rejected}
    if not fixes:
        return {}
    pattern = build_replacement_pattern(fixes)
    changes: dict[str, list[tuple[str, str]]] = {}

    # Data and config files too: in generated lists the README is rebuilt from
    # them, so a fix applied only to the README would be overwritten.
    for md_file in fix_targets(root):

        content = md_file.read_text(encoding="utf-8")
        seen: set[str] = set()
        file_fixes: list[tuple[str, str]] = []

        def replace(match: re.Match[str]) -> str:
            old = match.group(0)
            new = fixes[old]
            if old not in seen:
                file_fixes.append((old, new))
                seen.add(old)
            return new

        new_content = pattern.sub(replace, content)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")
            changes[str(md_file.relative_to(root))] = file_fixes

    return changes


def url_cell(url: str, max_len: int = 80) -> str:
    if len(url) <= max_len:
        return url
    return url[: max_len - 3] + "..."


def generate_pr_summary(
    redirect_fixes: dict[str, str],
    wayback_fixes: dict[str, str],
    changes: dict[str, list[tuple[str, str]]],
    unfixable: dict[str, list[dict]],
) -> str:
    lines: list[str] = [
        "## Auto-Fix Broken Links\n",
        "This PR was automatically generated by the link checker workflow.",
        "**Please review the changes before merging.**\n",
        "### Summary\n",
        f"- **{len(redirect_fixes)}** redirected URL(s) updated to final destination",
        f"- **{len(wayback_fixes)}** dead URL(s) replaced with Wayback Machine archives",
        f"- **{sum(len(v) for v in changes.values())}** total replacement(s) "
        f"across **{len(changes)}** file(s)\n",
    ]

    if redirect_fixes:
        lines += [
            "### Redirected URLs\n",
            "| Old URL | New URL |",
            "|---------|---------|",
        ]
        for old, new in sorted(redirect_fixes.items()):
            lines.append(f"| {url_cell(old)} | {url_cell(new)} |")
        lines.append("")

    if wayback_fixes:
        lines += [
            "### Dead URLs replaced with Wayback Machine archives\n",
            "| Dead URL | Archived URL |",
            "|----------|--------------|",
        ]
        for old, new in sorted(wayback_fixes.items()):
            lines.append(f"| {url_cell(old)} | {url_cell(new)} |")
        lines.append("")

    if unfixable:
        count = sum(len(v) for v in unfixable.values())
        lines += [
            f"### {count} link(s) could not be auto-fixed\n",
            "These require manual attention; a separate issue has been created.\n",
        ]
        for _source, entries in sorted(unfixable.items()):
            for entry in entries:
                lines.append(f"- `{entry['url']}` ({entry['status']})")
        lines.append("")

    return "\n".join(lines)


def generate_unfixable_report(unfixable: dict[str, list[dict]]) -> str:
    lines: list[str] = [
        "## Broken Links Requiring Manual Fix\n",
        "The following links could not be automatically fixed "
        "(no redirect detected and no Wayback Machine archive available).\n",
    ]
    for source, entries in sorted(unfixable.items()):
        lines.append(f"### `{source}`\n")
        for entry in entries:
            lines.append(f"- [ ] {entry['url']} -- {entry['status']}")
        lines.append("")

    return "\n".join(lines)


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"  ::output:: {name}={value}")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <lychee-report.json>", file=sys.stderr)
        sys.exit(1)

    report = load_report(sys.argv[1])
    root = Path(".")
    out_dir = Path("lychee")
    out_dir.mkdir(exist_ok=True)

    redirect_fixes = get_redirect_fixes(report)
    wayback_fixes = get_wayback_fixes(report)

    all_fixes: dict[str, str] = {}
    all_fixes.update(wayback_fixes)
    all_fixes.update(redirect_fixes)

    print(f"Redirect fixes : {len(redirect_fixes)}")
    print(f"Wayback fixes  : {len(wayback_fixes)}")

    for url in unsafe_data_fixes(all_fixes, root) if all_fixes else ():
        all_fixes.pop(url)
    unfixable = get_unfixable(report, set(all_fixes.keys()))

    if not all_fixes:
        print("No auto-fixable links found.")
        set_output("fixes_applied", "false")
        if unfixable:
            (out_dir / "unfixed-links.md").write_text(
                generate_unfixable_report(unfixable),
                encoding="utf-8",
            )
            set_output("has_unfixable", "true")
        else:
            set_output("has_unfixable", "false")
        return

    changes = apply_fixes(all_fixes, root)
    print(f"Files modified  : {len(changes)}")
    for filepath, fixes in sorted(changes.items()):
        for old, new in fixes:
            print(f"  {filepath}: {old} -> {new}")

    (out_dir / "fix-summary.md").write_text(
        generate_pr_summary(redirect_fixes, wayback_fixes, changes, unfixable),
        encoding="utf-8",
    )
    if unfixable:
        (out_dir / "unfixed-links.md").write_text(
            generate_unfixable_report(unfixable),
            encoding="utf-8",
        )

    set_output("fixes_applied", "true" if changes else "false")
    set_output("has_unfixable", "true" if unfixable else "false")


if __name__ == "__main__":
    main()

