#!/usr/bin/env python3
"""Render the pass table of an official-conformance run as Markdown.

The suite writes one ``checks.json`` per scenario under its ``-o`` directory:
``server-<scenario>-<time>/`` in server mode, ``<scenario>-<time>/`` in client
mode, where an ``auth/`` scenario sits one directory deeper.
This reads them and the baseline, and prints a table of what passed, what did
not, which non-passes are known, and what tracks each known one: the ``#N``
issue and ``SEP-N`` references in that baseline line's comment. It is
generated from the run so the table cannot drift from the result; nothing in
it is written by hand.

Usage:
  conformance_pass_table.py RESULTS_DIR BASELINE_YML [TITLE]
      [--suite VERSION] [--commit SHA] [--json OUT]
  conformance_pass_table.py --page LEG_JSON...

The first form prints one leg's table, for a job summary, and with ``--json``
also writes the leg as data. The second assembles legs written that way into
one Markdown document, for the docs page that publishes the pass table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_RUN_SUFFIX = re.compile(r"^server-|-\d{4}-\d{2}-\d{2}T.*$")
_PASSING = {"SUCCESS", "INFO"}
# Horizontal whitespace only: `\s` would let an entry's comment run on into the
# next line's section comment.
_ENTRY = re.compile(r"^[ \t]*-[ \t]+([A-Za-z0-9_./\-]+)[ \t]*(?:#(.*))?$", flags=re.MULTILINE)
_REF = re.compile(r"#\d+\b|SEP-\d+\b")


def _baseline(path: Path) -> dict[str, list[str]]:
    """Each listed scenario and the issues or SEPs its line comment references."""
    return {m.group(1): _REF.findall(m.group(2) or "") for m in _ENTRY.finditer(path.read_text())}


def _scenarios(results: Path) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for checks in sorted(results.glob("**/checks.json")):
        name = checks.parent.relative_to(results).as_posix()
        found[_RUN_SUFFIX.sub("", name)] = json.loads(checks.read_text())
    return found


def leg(results: Path, baseline: Path, title: str, suite: str = "", commit: str = "") -> dict:
    """One leg of a run as data: a row per scenario, with its verdict."""
    known = _baseline(baseline)
    rows = []
    for name, checks in sorted(_scenarios(results).items()):
        ok = sum(1 for c in checks if c["status"] in _PASSING)
        bad = len(checks) - ok
        if bad == 0:
            # The suite fails a run whose baseline lists a scenario that passes.
            verdict = "stale" if name in known else "pass"
        else:
            verdict = "known" if name in known else "REGRESSION"
        rows.append({"scenario": name, "passed": ok, "failed": bad, "verdict": verdict, "tracked": known.get(name, [])})
    return {"title": title, "baseline": baseline.name, "suite": suite, "commit": commit, "rows": rows}


def _headline(data: dict) -> str:
    rows = data["rows"]
    count = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("pass", "stale", "known", "REGRESSION")}
    parts = [
        f"{count['pass'] + count['stale']} of {len(rows)} scenarios pass",
        f"{count['known']} are known non-passes in `{data['baseline']}`",
    ]
    if count["stale"]:
        parts.append(f"{count['stale']} pass but are still listed there (stale)")
    parts.append(f"{count['REGRESSION']} are regressions")
    return "; ".join(parts) + "."


def _provenance(data: dict) -> str:
    bits = []
    if data["suite"]:
        bits.append(f"suite `@modelcontextprotocol/conformance@{data['suite']}`")
    if data["commit"]:
        bits.append(f"Hangar at `{data['commit'][:12]}`")
    return ("Run with " + ", ".join(bits) + ".") if bits else ""


def _table(data: dict) -> list[str]:
    lines = [f"### {data['title']}", "", _headline(data)]
    if provenance := _provenance(data):
        lines += ["", provenance]
    lines += [
        "",
        "| Scenario | Checks passed | Checks not passed | Result | Tracked by |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in data["rows"]:
        # One space each side, empty cells included: the docs lint compact tables (MD060).
        cells = [f"`{r['scenario']}`", str(r["passed"]), str(r["failed"]), r["verdict"], ", ".join(r["tracked"])]
        lines.append("|" + "|".join(f" {c} " if c else " " for c in cells) + "|")
    lines.append("")
    return lines


def render(results: Path, baseline: Path, title: str, suite: str = "", commit: str = "") -> str:
    return "\n".join(_table(leg(results, baseline, title, suite, commit)))


def page(legs: list[dict]) -> str:
    """Every leg in one document, in the order given, each under its own heading."""
    lines: list[str] = []
    for data in legs:
        lines += _table(data)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if "--page" in argv[1:]:
        files = [a for a in argv[1:] if a != "--page"]
        if not files:
            print(__doc__, file=sys.stderr)
            return 2
        sys.stdout.write(page([json.loads(Path(f).read_text()) for f in files]))
        return 0

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("results", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("title", nargs="?", default="MCP conformance")
    parser.add_argument("--suite", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--json", type=Path)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        print(__doc__, file=sys.stderr)
        return 2
    data = leg(args.results, args.baseline, args.title, args.suite, args.commit)
    if args.json:
        args.json.write_text(json.dumps(data, indent=2) + "\n")
    sys.stdout.write("\n".join(_table(data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
