#!/usr/bin/env python3
"""Give an upgrade note the version it shipped in.

A change that removes public API drops a `## Next — <headline>` section into
`UPGRADE.md` at PR time, next to the code that motivated it. Nothing ever gave
those sections a version: eight of them accumulated while 2.7.0, 2.8.0 and 2.9.0
shipped, and the changelog entries for those releases pointed a reader at
`UPGRADE.md` to find a section headed "Next" (#983).

Two failures came out of that, and both are why this runs at release time rather
than being someone's checklist item:

* **A reader cannot tell whether a "Next" section has shipped.** It reads the
  same before and after the release it describes.
* **The drafts go stale against each other.** The `builder()` note said
  "`MCPServerFactory` … unchanged by this release", true when it was written for
  #963 and false once #965 landed *in the same release*. Folding them into one
  section at release time is where that gets noticed.

Called from `assemble_release_changelog.sh`, in the same commit as the changelog
assembly, so a merged release PR carries versioned notes. Idempotent, because
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


def promote(path: Path, version: str) -> int:
    text = path.read_text(encoding="utf-8")

    if find_section(text, version) is not None:
        print(f"UPGRADE.md already has a section for {version}. Nothing to do.")
        return 0

    drafts, remainder = split_drafts(text)
    if not drafts:
        print(f"No `## Next` sections to promote to {version}.")
        return 0

    # Newest first, above whatever history the file already carries.
    anchor = SECTION_RE.search(remainder)
    cut = anchor.start() if anchor else len(remainder.rstrip("\n")) + 1
    head = remainder[:cut].rstrip("\n")
    tail = remainder[cut:].lstrip("\n")

    body = section_for(version, drafts)
    path.write_text(f"{head}\n\n{body}\n{tail}" if tail else f"{head}\n\n{body}", encoding="utf-8")

    print(f"Promoted {len(drafts)} draft(s) to `## Upgrade to {version}`:")
    for headline, _ in drafts:
        print(f"  - {headline}")
    print("\nRead the folded section before merging: drafts written against different")
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
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"error: {path} not found", file=sys.stderr)
        return 2

    return promote(path, args.version) if args.action == "promote" else extract(path, args.version)


if __name__ == "__main__":
    sys.exit(main())
