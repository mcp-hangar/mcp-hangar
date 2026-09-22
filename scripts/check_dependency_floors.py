#!/usr/bin/env python3
"""Keep the declared dependency floors honest. Two checks for the `deps-floor-audit` job.

`floors` -- no floor below what can be installed. pip leaves an installed
package alone while it satisfies the constraint, so a declared floor is what an
existing environment keeps on upgrade. We pin `mcp`, and where `mcp` declares a
higher floor for a package we also declare, ours describes an install the
resolver can never produce. Scanners that read `requires_dist` without resolving
it audit that fiction (pydantic `>=2.0.0` was reported for CVE-2024-3772 while
`mcp==2.0.0` required `>=2.12.0`), and the real exposure sits one level down at
mcp's floor instead of ours. So: for every package both declare, ours must be at
least theirs.

`audit-ignores` -- validates `.pip-audit-ignore.toml` and prints the matching
`--ignore-vuln` arguments. An entry needs an id, a reason (why the advisory is
not reachable here) and an expiry date; one past its date fails the build, so an
exception is re-argued rather than left to outlive the reason for it.

Usage:
    python scripts/check_dependency_floors.py floors
    python scripts/check_dependency_floors.py audit-ignores [--file .pip-audit-ignore.toml]
"""

from __future__ import annotations

import argparse
import datetime
import sys
import tomllib
from importlib.metadata import requires
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

OURS = "mcp-hangar"
PINNED = ("mcp",)
_FLOOR_OPERATORS = (">=", "==", "~=", "===", ">")


def floor(requirement: Requirement) -> Version | None:
    """The lowest version ``requirement`` admits, or None when it has no lower bound."""
    bounds = [Version(spec.version) for spec in requirement.specifier if spec.operator in _FLOOR_OPERATORS]
    return max(bounds, default=None)


def base_floors(requirements: list[str]) -> dict[str, list[tuple[Version | None, str]]]:
    """Floors per canonical name, from the requirements an install without extras pulls in.

    Extra-gated entries are dropped. Environment markers are not evaluated: a
    floor that applies only on some Python still has to hold there, so every
    variant is kept and compared.
    """
    floors: dict[str, list[tuple[Version | None, str]]] = {}
    for raw in requirements:
        req = Requirement(raw)
        if req.marker is not None and "extra" in str(req.marker):
            continue
        floors.setdefault(canonicalize_name(req.name), []).append((floor(req), raw))
    return floors


def fictions(ours: list[str], theirs: list[str], source: str) -> list[str]:
    """One message per package whose declared floor is below the one ``source`` imposes."""
    our_floors = base_floors(ours)
    problems = []
    for name, their_variants in sorted(base_floors(theirs).items()):
        for our_floor, our_raw in our_floors.get(name, []):
            for their_floor, their_raw in their_variants:
                if their_floor is None:
                    continue
                if our_floor is None or our_floor < their_floor:
                    problems.append(
                        f"{name}: we declare `{our_raw}` but {source} requires `{their_raw}`; "
                        f"raise our floor to at least {their_floor}"
                    )
    return problems


def check_floors() -> int:
    ours = requires(OURS) or []
    problems = [p for dist in PINNED for p in fictions(ours, requires(dist) or [], dist)]
    for problem in problems:
        print(f"floor fiction: {problem}", file=sys.stderr)
    if not problems:
        print(f"declared floors of {OURS} are at or above those of {', '.join(PINNED)}")
    return 1 if problems else 0


def ignore_args(path: Path, today: datetime.date) -> list[str]:
    """The ``--ignore-vuln`` arguments for ``path``, or ValueError naming every bad entry."""
    if not path.exists():
        return []
    entries = tomllib.loads(path.read_text()).get("ignore", [])
    errors, args = [], []
    for index, entry in enumerate(entries):
        vuln_id = entry.get("id")
        label = vuln_id or f"entry {index}"
        if not isinstance(vuln_id, str) or not vuln_id.strip():
            errors.append(f"{label}: missing `id`")
            continue
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            errors.append(f"{label}: missing `reason` (why it is not reachable here)")
        expires = entry.get("expires")
        if not isinstance(expires, datetime.date):
            errors.append(f"{label}: `expires` must be a date, e.g. expires = 2026-12-31")
        elif expires < today:
            errors.append(f"{label}: expired on {expires}; re-check it and extend the date, or remove the entry")
        args += ["--ignore-vuln", vuln_id]
    if errors:
        raise ValueError("\n".join(errors))
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("floors")
    ignores = sub.add_parser("audit-ignores")
    ignores.add_argument("--file", type=Path, default=Path(".pip-audit-ignore.toml"))
    args = parser.parse_args(argv)

    if args.command == "floors":
        return check_floors()
    try:
        print(" ".join(ignore_args(args.file, datetime.date.today())))
    except ValueError as exc:
        print(f"{args.file}:\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
