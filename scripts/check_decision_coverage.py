#!/usr/bin/env python3
"""Enforce per-module branch-coverage floors on the decision paths.

A decision path is a module where a wrong branch produces a wrong allow/deny:
authorization, human consent, egress and tool-access policy, and supply-chain
digest pinning. An untested branch there is a security defect, not a style
complaint, which is why those modules get a floor and the rest of the tree does
not.

This script exists because ``coverage.py``'s ``fail_under`` is **global**: there
is no per-file threshold. A single global number would have to be low enough for
the weakest module in the tree, which is exactly the modules that need the
strongest guarantee. So the floors live in ``[tool.decision_coverage.floors]``
and are checked here against a branch-mode ``coverage.json``.

Floors are the values MEASURED on the branch that introduced them, rounded down
-- not an aspirational 90. A gate that is red on the day it lands teaches people
to skip it. Raising a floor is the unit of progress; ``--bump`` rewrites them
from the current report so that progress can be locked in without hand-editing.

Usage::

    python scripts/check_decision_coverage.py coverage.json
    python scripts/check_decision_coverage.py coverage.json --bump
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _load_config() -> tuple[dict[str, float], float]:
    cfg = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["decision_coverage"]
    return dict(cfg["floors"]), float(cfg.get("drift_allowance", 3.0))


def _measured(report: dict) -> dict[str, float]:
    """Map module path -> combined line+branch coverage percentage."""
    out: dict[str, float] = {}
    for filename, data in report.get("files", {}).items():
        normalized = filename.replace("\\", "/")
        marker = "src/mcp_hangar/"
        if marker in normalized:
            normalized = normalized.split(marker, 1)[1]
        out[normalized] = float(data["summary"]["percent_covered"])
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=pathlib.Path, help="coverage.json from a branch-mode run")
    parser.add_argument(
        "--bump",
        action="store_true",
        help="rewrite the floors in pyproject.toml from this report (never lowers a floor)",
    )
    args = parser.parse_args(argv[1:])

    if not args.report.exists():
        print(f"error: {args.report} does not exist -- run pytest with --cov-branch first", file=sys.stderr)
        return 2

    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not report.get("meta", {}).get("branch_coverage", False):
        print(
            "error: this report was produced WITHOUT branch coverage. The floors are "
            "branch numbers; measuring statements only would silently pass a lower bar. "
            "Set `branch = true` under [tool.coverage.run].",
            file=sys.stderr,
        )
        return 2

    floors, drift_allowance = _load_config()
    measured = _measured(report)

    missing = sorted(m for m in floors if m not in measured)
    if missing:
        print("error: modules with a floor but no coverage data:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        print(
            "\nEither the module moved (update the floor key) or the test selection no "
            "longer reaches it. A decision path that is not measured is not gated.",
            file=sys.stderr,
        )
        return 2

    if args.bump:
        raised = _bump(floors, measured)
        print(f"raised {raised} floor(s) in pyproject.toml" if raised else "no floor moved")
        return 0

    below = [(m, measured[m], floors[m]) for m in sorted(floors) if measured[m] < floors[m]]
    drifted = [(m, measured[m], floors[m]) for m in sorted(floors) if measured[m] - floors[m] > drift_allowance]

    for module in sorted(floors):
        state = "FAIL" if measured[module] < floors[module] else "ok"
        print(f"  {state:4}  {measured[module]:6.2f}  (floor {floors[module]:5.1f})  {module}")

    if below:
        print("\nBranch coverage fell below the floor on a decision path:", file=sys.stderr)
        for module, got, floor in below:
            print(f"  {module}: {got:.2f} < {floor:.1f}", file=sys.stderr)
        print(
            "\nAdd the missing branch tests. Lowering a floor to make this pass is a reviewable decision, not a fix.",
            file=sys.stderr,
        )
        return 1

    if drifted:
        print(
            f"\nFloors are more than {drift_allowance:.0f} points below reality -- "
            f"a floor that far under the real number has stopped being a ratchet. "
            f"Run with --bump to lock the progress in:",
            file=sys.stderr,
        )
        for module, got, floor in drifted:
            print(f"  {module}: {got:.2f} vs floor {floor:.1f}", file=sys.stderr)
        return 1

    print(f"\nall {len(floors)} decision-path modules hold their floor")
    return 0


def _bump(floors: dict[str, float], measured: dict[str, float]) -> int:
    """Raise floors to the measured values. Never lowers."""
    text = PYPROJECT.read_text(encoding="utf-8")
    raised = 0
    for module in sorted(floors):
        new = math.floor(measured[module] * 10) / 10
        if new <= floors[module]:
            continue
        old_line = f'"{module}" = {floors[module]}'
        if old_line not in text:
            old_line = f'"{module}" = {floors[module]:.1f}'
        if old_line not in text:
            print(f"warning: could not locate the floor line for {module}", file=sys.stderr)
            continue
        text = text.replace(old_line, f'"{module}" = {new}', 1)
        raised += 1
    PYPROJECT.write_text(text, encoding="utf-8")
    return raised


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
