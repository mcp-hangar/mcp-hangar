#!/usr/bin/env python3
"""Render the pass table of an official-conformance run as Markdown.

The suite writes one ``checks.json`` per scenario under its ``-o`` directory:
``server-<scenario>-<time>/`` in server mode, ``<scenario>-<time>/`` in client
mode, where an ``auth/`` scenario sits one directory deeper.
This reads them and the baseline, and prints a table of what passed, what
failed (a FAILURE or WARNING, as the suite counts it), which failures are
known, and what tracks each known one: the ``#N`` issue and ``SEP-N``
references in that baseline line's comment. With ``--requirements`` (the
suite's frozen ``requirements/<revision>.yaml``) it also marks the scenarios
the suite runs but does not score for that revision, which count toward
neither the pass rate nor the exit code. It is generated from the run so the
table cannot drift from the result; nothing in it is written by hand.

Usage:
  conformance_pass_table.py RESULTS_DIR BASELINE_YML [TITLE]
      [--suite VERSION] [--commit SHA] [--requirements YAML] [--json OUT]
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
# What the suite itself counts against a scenario, in its baseline check and its
# exit code. A SKIPPED check (the client never sent what it would judge) counts
# neither way, so it is shown on its own rather than as a failure.
_FAILING = {"FAILURE", "WARNING"}
# Horizontal whitespace only: `\s` would let an entry's comment run on into the
# next line's section comment.
_ENTRY = re.compile(r"^[ \t]*-[ \t]+([A-Za-z0-9_./\-]+)[ \t]*(?:#(.*))?$", flags=re.MULTILINE)
_REF = re.compile(r"#\d+\b|SEP-\d+\b")
# A baseline file holds one role, as its single top-level key.
_ROLE = re.compile(r"^(server|client):", flags=re.MULTILINE)


def _baseline(path: Path) -> dict[str, list[str]]:
    """Each listed scenario and the issues or SEPs its line comment references."""
    return {m.group(1): _REF.findall(m.group(2) or "") for m in _ENTRY.finditer(path.read_text())}


def _not_scored(requirements: Path | None, role: str) -> dict[str, str]:
    """The scenarios the suite runs for this role but does not score, and why."""
    if requirements is None:
        return {}
    # Imported here so that `--page`, which reads only JSON, runs on a bare interpreter.
    import yaml

    entries = yaml.safe_load(requirements.read_text()).get("not_scored") or []
    return {e["scenario"]: e.get("reason", "") for e in entries if e.get("leg") == role}


def _scenarios(results: Path) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for checks in sorted(results.glob("**/checks.json")):
        name = checks.parent.relative_to(results).as_posix()
        found[_RUN_SUFFIX.sub("", name)] = json.loads(checks.read_text())
    return found


def leg(
    results: Path,
    baseline: Path,
    title: str,
    suite: str = "",
    commit: str = "",
    requirements: Path | None = None,
) -> dict:
    """One leg of a run as data: a row per scenario, with its verdict."""
    known = _baseline(baseline)
    found = _ROLE.search(baseline.read_text())
    role = found.group(1) if found else "server"
    unscored = _not_scored(requirements, role)
    rows = []
    for name, checks in sorted(_scenarios(results).items()):
        ok = sum(1 for c in checks if c["status"] in _PASSING)
        bad = sum(1 for c in checks if c["status"] in _FAILING)
        if name in unscored:
            # Neither its failure nor a stale baseline line moves the suite's exit code.
            reason = f": {unscored[name]}" if unscored[name] else ""
            verdict = f"{'fail' if bad else 'pass'}, not scored{reason}"
        elif bad == 0:
            # The suite fails a run whose baseline lists a scenario that passes.
            verdict = "stale" if name in known else "pass"
        else:
            verdict = "known" if name in known else "REGRESSION"
        rows.append(
            {
                "scenario": name,
                "passed": ok,
                "failed": bad,
                "skipped": len(checks) - ok - bad,
                "verdict": verdict,
                "scored": name not in unscored,
                "tracked": known.get(name, []),
            }
        )
    return {"title": title, "baseline": baseline.name, "suite": suite, "commit": commit, "rows": rows}


def _headline(data: dict) -> str:
    scored = [r for r in data["rows"] if r.get("scored", True)]
    unscored = len(data["rows"]) - len(scored)
    count = {v: sum(1 for r in scored if r["verdict"] == v) for v in ("pass", "stale", "known", "REGRESSION")}
    parts = [
        f"{count['pass'] + count['stale']} of {len(scored)} {'scored ' if unscored else ''}scenarios pass",
        f"{count['known']} are known failures in `{data['baseline']}`",
    ]
    if count["stale"]:
        parts.append(f"{count['stale']} pass but are still listed there (stale)")
    parts.append(f"{count['REGRESSION']} are regressions")
    line = "; ".join(parts) + "."
    if unscored:
        line += f" {unscored} more ran but are not scored for this revision."
    return line


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
        "| Scenario | Checks passed | Checks failed | Checks skipped | Result | Tracked by |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in data["rows"]:
        # One space each side, empty cells included: the docs lint compact tables (MD060).
        counts = [str(r["passed"]), str(r["failed"]), str(r["skipped"])]
        cells = [f"`{r['scenario']}`", *counts, r["verdict"], ", ".join(r["tracked"])]
        lines.append("|" + "|".join(f" {c} " if c else " " for c in cells) + "|")
    lines.append("")
    return lines


def render(
    results: Path,
    baseline: Path,
    title: str,
    suite: str = "",
    commit: str = "",
    requirements: Path | None = None,
) -> str:
    return "\n".join(_table(leg(results, baseline, title, suite, commit, requirements)))


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
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--json", type=Path)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        print(__doc__, file=sys.stderr)
        return 2
    data = leg(args.results, args.baseline, args.title, args.suite, args.commit, args.requirements)
    if args.json:
        args.json.write_text(json.dumps(data, indent=2) + "\n")
    sys.stdout.write("\n".join(_table(data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
