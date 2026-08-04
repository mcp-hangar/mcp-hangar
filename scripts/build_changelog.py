#!/usr/bin/env python3
"""Assemble CHANGELOG.md from the per-PR fragments in `changelog.d/`.

The problem this solves is mechanical. Every non-trivial PR used to write its
entry into `## [Unreleased]` in CHANGELOG.md -- the same anchor in the same
file -- so any two PRs open at once conflicted, and the second one to merge got
a hand-resolve. On top of that release-please generated its own terse section
from the commit subjects and inserted it *above* the hand-written block, which
left the prose orphaned below the release it belonged to: v2.3.0 shipped with
three `## [Unreleased]` blocks that had to be consolidated by hand.

A fragment is a file, so two PRs never touch the same bytes and git has nothing
to merge. release-please keeps the version, the tag and the release PR
(`skip-changelog: true` in release-please-config.json); this script owns the
changelog body, and `release-please.yml` runs it on the release branch.

Fragment naming -- `changelog.d/<id>-<slug>.<kind>.md`:

    changelog.d/748-dead-symbol-facade.fixed.md
    changelog.d/749-drop-event-sourced-pair.removed.md

`<kind>` is one of the six Keep a Changelog sections. `<id>` is the issue or PR
number and is only a sort key and a fallback: the PR link is normally read from
the squash commit that added the file, so a fragment written before the PR
number is known still gets linked correctly.

The fragment body is the bullet text without the leading `- ` and without the
trailing PR link -- this script adds both. `changelog.d/_summary.md` is
optional and becomes the release's intro paragraph, the way v2.3.0 has one.

Usage:
    python scripts/build_changelog.py check [PATH ...]
    python scripts/build_changelog.py preview --version 2.4.0
    python scripts/build_changelog.py assemble --version 2.4.0
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
import re
import subprocess
import sys

REPO_URL = "https://github.com/mcp-hangar/mcp-hangar"
FRAGMENT_DIR = Path("changelog.d")
CHANGELOG_PATH = Path("CHANGELOG.md")
SUMMARY_NAME = "_summary.md"

# Keep a Changelog order, which is what the file's own preamble claims to
# follow. release-please emitted Added/Fixed/Changed instead, an artifact of the
# order its `changelog-sections` config happened to list.
SECTIONS = ("added", "changed", "deprecated", "removed", "fixed", "security")
SECTION_TITLES = {
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "removed": "Removed",
    "fixed": "Fixed",
    "security": "Security",
}

FRAGMENT_RE = re.compile(r"^(?P<stem>[A-Za-z0-9._-]+)\.(?P<kind>[a-z]+)\.md$")
LEADING_ID_RE = re.compile(r"^(\d+)")
PR_IN_SUBJECT_RE = re.compile(r"\(#(\d+)\)")
VERSION_HEADING_RE = re.compile(r"^## \[")
UNRELEASED_HEADING_RE = re.compile(r"^## \[Unreleased\]")
UNRELEASED_LINK_RE = re.compile(r"^\[Unreleased\]: ")


class FragmentError(Exception):
    """A fragment that cannot be rendered. Reported with its path."""


@dataclass(frozen=True)
class Fragment:
    path: Path
    kind: str
    ident: int | None
    stem: str
    text: str
    pr: int | None

    @property
    def sort_key(self) -> tuple[int, int, str]:
        # Sections are rendered newest-first (descending id), which is the order
        # the hand-written entries already used. The leading flag keeps an
        # unnumbered fragment at the bottom of its section under that reversal
        # rather than at the top.
        return (1 if self.ident is not None else 0, self.ident or 0, self.stem)


def _run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _pr_from_git(path: Path) -> int | None:
    """Read the PR number from the squash commit that ADDED this fragment.

    Squash merges append `(#N)` to the subject, so the number is recoverable
    without the author having to know it when they name the file. Returns None
    on a fragment that is not committed yet -- the local `make changelog`
    preview path -- where the filename prefix is the only source.
    """
    subject = _run("git", "log", "--diff-filter=A", "--format=%s", "-1", "--", str(path))
    match = PR_IN_SUBJECT_RE.search(subject)
    return int(match.group(1)) if match else None


def parse_fragment(path: Path) -> Fragment:
    match = FRAGMENT_RE.match(path.name)
    if not match:
        raise FragmentError(f"{path}: name must be <id>-<slug>.<kind>.md, e.g. 748-dead-symbol-facade.fixed.md")
    kind = match.group("kind")
    if kind not in SECTIONS:
        raise FragmentError(f"{path}: unknown kind '{kind}'. Use one of: {', '.join(SECTIONS)}")

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise FragmentError(f"{path}: fragment is empty")
    if text.startswith(("- ", "* ", "#")):
        raise FragmentError(
            f"{path}: write the entry text only -- no leading bullet or heading, "
            "the assembler adds the bullet, the section and the PR link"
        )

    stem = match.group("stem")
    ident_match = LEADING_ID_RE.match(stem)
    ident = int(ident_match.group(1)) if ident_match else None
    pr = _pr_from_git(path) or ident
    return Fragment(path=path, kind=kind, ident=ident, stem=stem, text=text, pr=pr)


def load_fragments(directory: Path = FRAGMENT_DIR) -> list[Fragment]:
    if not directory.is_dir():
        return []
    paths = sorted(p for p in directory.glob("*.md") if p.name not in {SUMMARY_NAME, "README.md"})
    return [parse_fragment(p) for p in paths]


def render_bullet(fragment: Fragment) -> str:
    """One markdown list item.

    Continuation lines are indented two spaces so a multi-paragraph fragment
    stays inside its bullet instead of breaking the list.
    """
    lines = fragment.text.splitlines()
    body = lines[0]
    for line in lines[1:]:
        body += "\n" + (f"  {line}" if line.strip() else "")
    if fragment.pr is not None:
        body += f" ([#{fragment.pr}]({REPO_URL}/pull/{fragment.pr}))"
    return f"- {body}"


def previous_tag(version: str) -> str | None:
    tags = _run("git", "tag", "--sort=-v:refname").splitlines()
    for tag in tags:
        if tag and tag != f"v{version}":
            return tag
    return None


def render_section(
    version: str,
    fragments: list[Fragment],
    *,
    date: str,
    prev_tag: str | None,
    summary: str | None,
) -> str:
    if prev_tag:
        heading = f"## [{version}]({REPO_URL}/compare/{prev_tag}...v{version}) ({date})"
    else:
        heading = f"## [{version}]({REPO_URL}/releases/tag/v{version}) ({date})"

    parts = [heading, ""]
    if summary:
        parts += [summary.strip(), ""]
    for kind in SECTIONS:
        in_section = sorted((f for f in fragments if f.kind == kind), key=lambda f: f.sort_key, reverse=True)
        if not in_section:
            continue
        parts.append(f"### {SECTION_TITLES[kind]}")
        parts.append("")
        parts += [render_bullet(f) for f in in_section]
        parts.append("")
    return "\n".join(parts)


def insert_section(changelog: str, section: str) -> str:
    """Put the new section directly above the newest existing one.

    Also drops a `## [Unreleased]` heading and its reference link if either is
    still present. Under the fragment model there is no Unreleased block to
    keep: unreleased work lives in `changelog.d/`, which is the whole point.
    An empty heading left behind would be a second place to look.
    """
    lines = changelog.splitlines()
    kept: list[str] = []
    skipping_unreleased = False
    for line in lines:
        if UNRELEASED_HEADING_RE.match(line):
            skipping_unreleased = True
            continue
        if skipping_unreleased:
            # Everything up to the next version heading belonged to Unreleased.
            # Reaching one with content still pending means the migration was
            # not run; refuse rather than silently discard prose.
            if VERSION_HEADING_RE.match(line):
                skipping_unreleased = False
            elif line.strip():
                raise FragmentError(
                    "CHANGELOG.md still has content under `## [Unreleased]`. Move it into "
                    "changelog.d/ fragments first -- refusing to discard it."
                )
            else:
                continue
        if UNRELEASED_LINK_RE.match(line):
            continue
        kept.append(line)

    # One trailing blank line, however the section was rendered: `splitlines()`
    # drops a trailing newline, which would run the last bullet straight into
    # the heading of the previous release.
    block = [*section.rstrip("\n").splitlines(), ""]

    for index, line in enumerate(kept):
        if VERSION_HEADING_RE.match(line):
            body = kept[:index] + block + kept[index:]
            break
    else:
        body = kept + ["", *block]
    return "\n".join(body).rstrip("\n") + "\n"


def cmd_check(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.paths] if args.paths else None
    errors: list[str] = []
    if paths is None:
        try:
            fragments = load_fragments()
        except FragmentError as exc:
            errors.append(str(exc))
            fragments = []
    else:
        fragments = []
        for path in paths:
            try:
                fragments.append(parse_fragment(path))
            except FragmentError as exc:
                errors.append(str(exc))

    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    if errors:
        return 1
    print(f"OK: {len(fragments)} changelog fragment(s) valid")
    return 0


def _build(args: argparse.Namespace) -> str:
    fragments = load_fragments()
    if not fragments:
        # Not an error. A release carrying only dependency bumps, CI or docs
        # changes legitimately has no fragments, and so does the first release
        # cut after this mechanism landed. Failing here would turn both into a
        # red release run for nothing.
        return ""
    summary_path = FRAGMENT_DIR / SUMMARY_NAME
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else None
    date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
    prev = args.previous_tag or previous_tag(args.version)
    return render_section(args.version, fragments, date=date, prev_tag=prev, summary=summary)


def cmd_preview(args: argparse.Namespace) -> int:
    section = _build(args)
    print(section if section else "No fragments in changelog.d/; this release would add no section.")
    return 0


def cmd_assemble(args: argparse.Namespace) -> int:
    changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
    if re.search(rf"^## \[{re.escape(args.version)}\]", changelog, flags=re.MULTILINE):
        # Idempotent on purpose: release-please force-pushes its branch on every
        # push to main and this runs again right after, so a no-op has to be a
        # success rather than a duplicated section.
        print(f"CHANGELOG.md already has a section for {args.version}; nothing to do")
        return 0

    section = _build(args)
    if not section:
        print("No fragments in changelog.d/; leaving CHANGELOG.md alone.")
        return 0
    CHANGELOG_PATH.write_text(insert_section(changelog, section), encoding="utf-8")

    consumed = [f.path for f in load_fragments()]
    summary_path = FRAGMENT_DIR / SUMMARY_NAME
    if summary_path.exists():
        consumed.append(summary_path)
    for path in consumed:
        path.unlink()
    print(f"Assembled {len(consumed)} fragment(s) into CHANGELOG.md as {args.version}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate fragments (all, or the paths given)")
    check.add_argument("paths", nargs="*")
    check.set_defaults(func=cmd_check)

    for name, func, help_text in (
        ("preview", cmd_preview, "render the section to stdout without touching CHANGELOG.md"),
        ("assemble", cmd_assemble, "write the section into CHANGELOG.md and delete the fragments"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--version", required=True, help="release version, without the v prefix")
        cmd.add_argument("--date", help="release date (default: today, UTC)")
        cmd.add_argument("--previous-tag", help="tag for the compare link (default: newest tag)")
        cmd.set_defaults(func=func)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FragmentError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
