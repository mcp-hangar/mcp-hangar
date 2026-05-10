#!/usr/bin/env python3
"""Enforce the core -> enterprise import boundary.

Core (src/mcp_hangar/) MUST NOT statically import from enterprise/. This script:
  1. Scans src/ for `from enterprise...` and `import enterprise...` statements.
  2. Compares findings against a known-debt allowlist (this file).
  3. Fails if a NEW violation appears outside the allowlist, OR if an
     allowlist entry has been resolved (stale entry).

The allowlist is intentionally explicit -- every entry is tech debt with a
removal target. See TASK-P0-2 for the refactor plan.

Dynamic imports via importlib (e.g. _import_attribute("enterprise.X")) are
NOT detected by this script and are the preferred pattern; see
src/mcp_hangar/server/bootstrap/enterprise.py for the canonical approach.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mcp_hangar"

# Known violations -- each entry is a relative path under src/mcp_hangar/.
# Goal: empty list (TASK-P0-2 complete).
ALLOWLIST: frozenset[str] = frozenset()

# Matches: `from enterprise...`, `from enterprise.X...`, `import enterprise...`.
# Does NOT match string-based dynamic imports like _import_attribute("enterprise.X").
PATTERN = re.compile(r"^\s*(?:from\s+enterprise(?:\.[\w.]+)?\s+import|import\s+enterprise(?:\.[\w.]+)?)\b")


def scan() -> tuple[set[str], set[str]]:
    """Return (current_violators, stale_allowlist_entries) as relative paths."""
    current: set[str] = set()
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC).as_posix()
        try:
            text = py.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line in text.splitlines():
            if PATTERN.match(line):
                current.add(rel)
                break
    stale = set(ALLOWLIST - current)
    return current, stale


def main() -> int:
    current, stale = scan()
    new_violations = current - ALLOWLIST

    if new_violations:
        print("FAIL: new core->enterprise import violations detected:", file=sys.stderr)
        for path in sorted(new_violations):
            print(f"  src/mcp_hangar/{path}", file=sys.stderr)
        print(
            "\nCore (src/) MUST NOT statically import from enterprise/.\n"
            "Use dynamic imports via importlib instead -- see\n"
            "src/mcp_hangar/server/bootstrap/enterprise.py:_import_attribute()\n"
            "for the canonical pattern.",
            file=sys.stderr,
        )
        return 1

    if stale:
        print("FAIL: allowlist entries no longer needed (refactor merged?):", file=sys.stderr)
        for path in sorted(stale):
            print(f"  {path}", file=sys.stderr)
        print(
            "\nRemove these entries from ALLOWLIST in tools/check_enterprise_imports.py.",
            file=sys.stderr,
        )
        return 1

    debt = len(current)
    if debt:
        print(f"OK: {debt} known violation(s) on allowlist; no new violations.")
    else:
        print("OK: no core->enterprise imports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
