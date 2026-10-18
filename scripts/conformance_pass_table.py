#!/usr/bin/env python3
"""Render the pass table of an official-conformance run as Markdown.

The suite writes one ``checks.json`` per scenario under its ``-o`` directory.
This reads them and the baseline, and prints a table of what passed, what did
not, and which non-passes are known. It is generated from the run so the table
cannot drift from the result; nothing in it is written by hand.

Usage: conformance_pass_table.py RESULTS_DIR BASELINE_YML [TITLE]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_RUN_SUFFIX = re.compile(r"^server-|-\d{4}-\d{2}-\d{2}T.*$")
_PASSING = {"SUCCESS", "INFO"}


def _baseline(path: Path) -> set[str]:
    return set(re.findall(r"^\s*-\s+([A-Za-z0-9_.\-]+)", path.read_text(), flags=re.MULTILINE))


def _scenarios(results: Path) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for checks in sorted(results.glob("server-*/checks.json")):
        found[_RUN_SUFFIX.sub("", checks.parent.name)] = json.loads(checks.read_text())
    return found


def render(results: Path, baseline: Path, title: str) -> str:
    known = _baseline(baseline)
    scenarios = _scenarios(results)
    rows = []
    passed = 0
    for name, checks in sorted(scenarios.items()):
        ok = sum(1 for c in checks if c["status"] in _PASSING)
        bad = len(checks) - ok
        if bad == 0:
            passed += 1
            verdict = "pass"
        else:
            verdict = "known" if name in known else "REGRESSION"
        rows.append(f"| `{name}` | {ok} | {bad} | {verdict} |")
    lines = [
        f"### {title}",
        "",
        f"{passed} of {len(scenarios)} scenarios pass; "
        f"{len(scenarios) - passed} are known non-passes in `{baseline.name}`.",
        "",
        "| Scenario | Checks passed | Checks not passed | Result |",
        "|----------|---------------|-------------------|--------|",
        *rows,
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    title = argv[3] if len(argv) > 3 else "MCP conformance"
    sys.stdout.write(render(Path(argv[1]), Path(argv[2]), title))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
