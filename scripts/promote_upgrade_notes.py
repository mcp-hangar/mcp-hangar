#!/usr/bin/env python3
"""Give an upgrade note the version it shipped in.

A change a reader has to act on -- one that removes public API, or changes what
an existing configuration does -- carries an upgrade note, written at PR time
next to the code that motivated it. The note is one new file:

    upgrade.d/<issue>-<slug>.md     first line `### <headline>`, then the body

It used to be a `## Next — <headline>` section at the top of `UPGRADE.md`, and
that is why it is a file now: every PR with a note wrote at the same spot, so
any two of them conflicted and each merge left most open PRs dirty. A new file
gives git nothing to merge, the way `changelog.d/` already does for the
changelog. `## Next` sections are still read, so a PR opened before the move
still ships its note -- it only goes on conflicting until it moves it.

Nothing ever gave those notes a version: eight `## Next` sections accumulated
while 2.7.0, 2.8.0 and 2.9.0 shipped, and the changelog entries for those
releases pointed a reader at `UPGRADE.md` to find a section headed "Next"
(#983). Two failures came out of that, and both are why this runs at release
time rather than being someone's checklist item:

* **A reader cannot tell whether a "Next" note has shipped.** It reads the same
  before and after the release it describes.
* **The drafts go stale against each other.** The `builder()` note said
  "`MCPServerFactory` … unchanged by this release", true when it was written for
  #963 and false once #965 landed *in the same release*. Folding them into one
  section at release time is where that gets noticed.

`promote` folds every pending note into one `## Upgrade to <version>` section --
the legacy `## Next` sections first, in file order, then the fragments by file
name -- and deletes the fragments it consumed. It is called from
`assemble_release_changelog.sh`, in the same commit as the changelog assembly,
so a merged release PR carries versioned notes. Idempotent, because
release-please force-pushes its branch and this reruns on a tree it has already
rewritten.

`extract` prints one version's section on stdout. That is what the docs repo's
sync consumes: `docs/upgrade.md` is the published guide and holds history this
file never had (1.3.0 through 2.6.0), so it is prepended to, never replaced.

Usage:
    python scripts/promote_upgrade_notes.py promote --version 2.10.0
    python scripts/promote_upgrade_notes.py extract --version 2.10.0
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DRAFT_RE = re.compile(r"^## Next\s*[—-]\s*(?P<headline>.+?)\s*$", re.M)
SECTION_RE = re.compile(r"^## ", re.M)

FRAGMENT_DIR = "upgrade.d"
FRAGMENT_HEADING_RE = re.compile(r"^### (?P<headline>\S.*?)\s*$")


class FragmentError(ValueError):
    """A fragment the promoter cannot fold without guessing what it says."""


def split_drafts(text: str) -> tuple[list[tuple[str, str]], str]:
    """(headline, body) per `## Next` section, and the text with them removed."""
    drafts: list[tuple[str, str]] = []
    keep: list[str] = []
    pos = 0

    for match in DRAFT_RE.finditer(text):
        keep.append(text[pos : match.start()])
        after = match.end()
        following = SECTION_RE.search(text, after)
        end = following.start() if following else len(text)
        drafts.append((match.group("headline"), text[after:end].strip("\n")))
        pos = end

    keep.append(text[pos:])
    return drafts, "".join(keep)


def read_fragments(directory: Path) -> list[tuple[Path, str, str]]:
    """(path, headline, body) per fragment in `directory`, ordered by file name.

    The README documents the format and is not a note. A directory that does
    not exist holds no notes.
    """
    if not directory.is_dir():
        return []

    fragments: list[tuple[Path, str, str]] = []
    for path in sorted(directory.glob("*.md"), key=lambda p: p.name):
        if path.name == "README.md":
            continue
        first, _, rest = path.read_text(encoding="utf-8").partition("\n")
        match = FRAGMENT_HEADING_RE.match(first)
        if match is None:
            raise FragmentError(f"{path}: the first line must be `### <headline>`, not {first!r}")
        body = rest.strip("\n")
        if not body:
            raise FragmentError(f"{path}: `{first.strip()}` has no body")
        fragments.append((path, match.group("headline"), body))
    return fragments


def section_for(version: str, drafts: list[tuple[str, str]]) -> str:
    parts = [f"## Upgrade to {version}\n"]
    for headline, body in drafts:
        parts.append(f"\n### {headline}\n\n{body}\n")
    return "".join(parts)


def find_section(text: str, version: str) -> str | None:
    start = text.find(f"## Upgrade to {version}\n")
    if start == -1:
        return None
    following = SECTION_RE.search(text, start + 3)
    return text[start : following.start() if following else len(text)].rstrip("\n") + "\n"


def promote(path: Path, version: str, fragment_dir: Path | None = None) -> int:
    directory = path.parent / FRAGMENT_DIR if fragment_dir is None else fragment_dir
    text = path.read_text(encoding="utf-8")

    # Read every fragment before writing anything: a malformed one fails the
    # release job with the guide and the fragments exactly as they were.
    try:
        fragments = read_fragments(directory)
    except FragmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    drafts, remainder = split_drafts(text)
    notes = drafts + [(headline, body) for _, headline, body in fragments]

    if find_section(text, version) is not None:
        print(f"UPGRADE.md already has a section for {version}. Nothing to do.")
        if notes:
            # A rerun on its own output finds nothing pending. Notes here were
            # added after the section was written; folding them in could stamp
            # them with a version they do not ship in, and deleting them would
            # lose them, so they stay where they are, and say so.
            print(f"::warning::{len(notes)} upgrade note(s) left pending: {version} already has a section.")
            for headline, _ in notes:
                print(f"  - {headline}")
        return 0

    if not notes:
        print(f"No upgrade notes to promote to {version}.")
        return 0

    # Newest first, above whatever history the file already carries.
    anchor = SECTION_RE.search(remainder)
    cut = anchor.start() if anchor else len(remainder.rstrip("\n")) + 1
    head = remainder[:cut].rstrip("\n")
    tail = remainder[cut:].lstrip("\n")

    body = section_for(version, notes)
    path.write_text(f"{head}\n\n{body}\n{tail}" if tail else f"{head}\n\n{body}", encoding="utf-8")

    # Only after the guide holds them: a fragment deleted first would be a note
    # lost if the write failed.
    for fragment, _, _ in fragments:
        fragment.unlink()

    print(f"Promoted {len(notes)} note(s) to `## Upgrade to {version}`:")
    for headline, _ in notes:
        print(f"  - {headline}")
    print("\nRead the folded section before merging: notes written against different")
    print("PRs can contradict each other once they land in one release.")
    return 0


def extract(path: Path, version: str) -> int:
    section = find_section(path.read_text(encoding="utf-8"), version)
    if section is None:
        print(f"error: no `## Upgrade to {version}` section in {path}", file=sys.stderr)
        return 1
    sys.stdout.write(section)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("promote", "extract"))
    parser.add_argument("--version", required=True, help="The version being released, e.g. 2.10.0.")
    parser.add_argument("--file", default="UPGRADE.md", help="Path to the upgrade guide.")
    parser.add_argument(
        "--fragments",
        default=None,
        help=f"Directory of upgrade-note fragments (default: {FRAGMENT_DIR}/ next to --file).",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"error: {path} not found", file=sys.stderr)
        return 2

    if args.action == "extract":
        return extract(path, args.version)
    return promote(path, args.version, Path(args.fragments) if args.fragments else None)


if __name__ == "__main__":
    sys.exit(main())
