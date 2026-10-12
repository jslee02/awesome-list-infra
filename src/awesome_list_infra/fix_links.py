"""Parse lychee JSON reports and fix broken links in markdown files."""

from __future__ import annotations

import json
import os
import re
import subprocess
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


# A match must end where the URL ends, so fixing ".../a" leaves ".../abc",
# ".../a.html", ".../a,b", and ".../a(1)" alone. ")" and quotes can't count as
# URL characters: they close Markdown links and quoted YAML values. URLs can't
# contain spaces, so "." or "," followed by whitespace, a closing bracket, or the
# end is punctuation (end of a sentence, a flow-YAML separator), not URL.
URL_CONTINUES = r"(?![A-Za-z0-9_\-~%!$&*+;=:@/?#(]|[.,](?=[^\s\]}]))"
# Nor does a match start inside another URL, e.g. https://web.archive.org/web/2020/https://old.example.
URL_STARTS = r"(?<![A-Za-z0-9_\-~%!$&*+;=@/?#.])"  # not ":", which "[ref]:url" puts before a URL


def build_replacement_pattern(fixes: dict[str, str]) -> re.Pattern[str]:
    sorted_urls = sorted(fixes.keys(), key=len, reverse=True)
    return re.compile(URL_STARTS + "(?:" + "|".join(re.escape(url) for url in sorted_urls) + ")" + URL_CONTINUES)


def read_text(path: Path) -> str:
    """File text with its line endings untouched (CRLF files stay CRLF)."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


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

    seen: set[tuple[int, int]] = set()  # anchors can make the tree cyclic

    def same(a, b) -> bool:
        if (id(a), id(b)) in seen:
            return True
        seen.add((id(a), id(b)))
        if isinstance(a, dict):
            return isinstance(b, dict) and list(a) == list(b) and all(same(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return isinstance(b, list) and len(a) == len(b) and all(map(same, a, b))
        if isinstance(a, str):
            return b == fix(a)
        return a == b

    return same(old, new)


def unsafe_data_fixes(fixes: dict[str, str], root: Path) -> set[str]:
    """URLs that can't be fixed cleanly in a YAML file, so they're left unfixed everywhere.

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
        content = read_text(path)
        # A URL present in the data that no match here would replace (it continues past the
        # fixed prefix, or sits in compact flow YAML like "url: x,next: y") can't be fixed
        # consistently, so leave it everywhere rather than fix only the generated Markdown.
        # Copies embedded in other URLs (an archive link) are other links and don't count.
        matched = set(build_replacement_pattern(fixes).findall(content))
        if unmatched := {url for url in fixes if re.search(URL_STARTS + re.escape(url), content)} - matched:
            print(f"  not fixing {sorted(unmatched)}: {path} has them where they can't be replaced cleanly", file=sys.stderr)
            rejected |= unmatched
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

        content = read_text(md_file)
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
            write_text(md_file, new_content)
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


OUTPUT_EXCLUDES = [":(exclude)lychee", ":(exclude).awesome-list-infra"]


def reverted_fixes(applied: dict[str, str], root: Path) -> list[str]:
    """Fixed URLs that are back in Markdown, e.g. because a README generator emits them itself."""
    markdown = [read_text(f) for f in fix_targets(root) if f.suffix == ".md"]
    return sorted(old for old in applied
                  if any(build_replacement_pattern({old: ""}).search(text) for text in markdown))


def verify_after_regeneration(applied_path: Path, root: Path, out_dir: Path) -> None:
    """Report fixes the README generator undid, and whether any fix is left for a PR."""
    applied = json.loads(applied_path.read_text(encoding="utf-8"))
    reverted = reverted_fixes(applied, root)
    if reverted:
        report = out_dir / "unfixed-links.md"
        lines = [
            "### Restored by the README generator\n",
            "These links come from the generator itself, so fix them there:\n",
            *(f"- [ ] {old} -> {applied[old]}" for old in reverted),
            "",
        ]
        with open(report, "a", encoding="utf-8") as f:
            if report.stat().st_size if report.exists() else 0:
                f.write("\n")
            f.write("\n".join(lines))
    # Ignore this job's own outputs: the lychee/ reports and the infra checkout.
    changed = subprocess.run(["git", "status", "--porcelain", "--", ".", *OUTPUT_EXCLUDES], cwd=root,
                             capture_output=True, text=True, check=True).stdout.strip()
    set_output("fixes_applied", "true" if changed else "false")
    set_output("has_unfixable", "true" if reverted else "false")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify_after_regeneration(Path(sys.argv[2]), Path("."), Path("lychee"))
        return
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <lychee-report.json> | --verify <applied-fixes.json>", file=sys.stderr)
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
    changes = apply_fixes(all_fixes, root) if all_fixes else {}
    applied = {old: new for fixes in changes.values() for old, new in fixes}
    # Only links actually replaced count as fixed: one whose fix matched nowhere is reported too.
    unfixable = get_unfixable(report, set(applied))

    if not changes:
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

    (out_dir / "applied-fixes.json").write_text(json.dumps(applied, indent=2), encoding="utf-8")
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

