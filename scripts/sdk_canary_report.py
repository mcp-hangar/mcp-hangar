#!/usr/bin/env python3
"""Turn the SDK canary's JUnit file into one issue that is opened, updated or closed (#1793).

`mcp` and `mcp-types` are pinned exactly, so a new SDK release gives no signal
until someone moves the pin. The `sdk-canary` workflow installs the newest pair
over the locked environment, runs the unit and integration tiers, and calls this
with the result:

* failing: open one issue titled ``sdk canary: mcp <version>`` listing the
  failing tests grouped by their first frame in Hangar's own code, or update the
  open one (its title follows the version it last failed against);
* green: close any open canary issue, with a note naming the version.

Usage:
    python scripts/sdk_canary_report.py --junit junit.xml --sdk-version 2.4.0 \\
        --repo mcp-hangar/mcp-hangar --run-url https://... [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

TITLE_PREFIX = "sdk canary:"

#: `src/mcp_hangar/x/y.py:123: in function` -- a pytest `--tb=short` frame.
_FRAME = re.compile(r"^(?P<path>(?:src/)?mcp_hangar/\S+?\.py):(?P<line>\d+): in (?P<func>\S+)", re.MULTILINE)
_ERROR_LINE = re.compile(r"^E\s+(?P<text>\S.*)$", re.MULTILINE)

#: What the issue lists before it says "and N more".
_LISTED = 15

Gh = Callable[[list[str]], str]


@dataclass(frozen=True)
class Failure:
    test: str
    frame: str
    error: str


def first_frame(report: str) -> str:
    """The first traceback frame inside Hangar's package, else the exception line, else "unknown"."""
    frame = _FRAME.search(report)
    if frame:
        return f"{frame['path']} in {frame['func']}"
    error = _ERROR_LINE.search(report)
    return error["text"][:120] if error else "unknown"


def parse_junit(path: Path) -> tuple[list[Failure], int]:
    """The failures and errors in a JUnit file, and how many tests ran."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    ran = sum(int(s.get("tests", 0)) for s in suites)
    failures: list[Failure] = []
    for case in root.iter("testcase"):
        for node in (*case.findall("failure"), *case.findall("error")):
            report = (node.text or "") + "\n" + (node.get("message") or "")
            error = _ERROR_LINE.search(report)
            message = (node.get("message") or "").splitlines()
            failures.append(
                Failure(
                    test=f"{case.get('classname', '')}::{case.get('name', '')}".strip(":"),
                    frame=first_frame(report),
                    error=(error["text"] if error else (message[0] if message else ""))[:200],
                )
            )
    return failures, ran


def group_by_frame(failures: list[Failure]) -> dict[str, list[Failure]]:
    grouped: dict[str, list[Failure]] = defaultdict(list)
    for failure in failures:
        grouped[failure.frame].append(failure)
    return dict(sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])))


def render(version: str, failures: list[Failure], ran: int, run_url: str) -> tuple[str, str]:
    """The issue title and body for a failing run."""
    title = f"{TITLE_PREFIX} mcp {version}"
    lines = [
        f"The newest SDK, `mcp` {version} with its matching `mcp-types`, fails {len(failures)} of {ran} tests "
        "in the unit and integration tiers against current main, where the pinned SDK passes.",
        "",
        f"Run: {run_url}",
        "",
        "Failing tests, grouped by the first frame in Hangar's code:",
    ]
    for frame, group in group_by_frame(failures).items():
        lines += ["", f"### `{frame}` ({len(group)})", ""]
        lines += [f"- `{f.test}`" + (f": {f.error}" if f.error else "") for f in group[:_LISTED]]
        if len(group) > _LISTED:
            lines.append(f"- and {len(group) - _LISTED} more")
    lines += [
        "",
        "This issue is kept up to date by the `sdk-canary` workflow and closes itself when the newest SDK passes.",
    ]
    return title, "\n".join(lines)


def run_gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=60).stdout


def open_canary_issues(gh: Gh, repo: str) -> list[dict]:
    out = gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f'"{TITLE_PREFIX}" in:title',
            "--json",
            "number,title",
            "--limit",
            "20",
        ]
    )
    return [i for i in json.loads(out or "[]") if str(i["title"]).startswith(TITLE_PREFIX)]


def sync_issue(gh: Gh, repo: str, version: str, failures: list[Failure], ran: int, run_url: str) -> str:
    """Open, update or close the canary issue so it matches this run. Returns what it did."""
    existing = open_canary_issues(gh, repo)
    if failures:
        title, body = render(version, failures, ran, run_url)
        if existing:
            keep, *extra = existing
            gh(["issue", "edit", str(keep["number"]), "--repo", repo, "--title", title, "--body", body])
            for duplicate in extra:
                gh(
                    [
                        "issue",
                        "close",
                        str(duplicate["number"]),
                        "--repo",
                        repo,
                        "--comment",
                        f"Superseded by #{keep['number']}.",
                    ]
                )
            return f"updated #{keep['number']}"
        out = gh(["issue", "create", "--repo", repo, "--title", title, "--body", body])
        return f"opened {out.strip()}"
    for issue in existing:
        gh(
            [
                "issue",
                "close",
                str(issue["number"]),
                "--repo",
                repo,
                "--comment",
                f"The newest SDK, mcp {version}, passes the unit and integration tiers again ({run_url}). Closing.",
            ]
        )
    return f"closed {len(existing)} issue(s)" if existing else "nothing open, nothing to do"


def main(argv: list[str] | None = None, gh: Gh = run_gh) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--sdk-version", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--dry-run", action="store_true", help="print the issue instead of touching GitHub")
    args = parser.parse_args(argv)

    failures, ran = parse_junit(args.junit)
    if ran == 0:
        # A run that executed nothing is not a green run: do not close an open issue on it.
        print("The JUnit file holds no tests; leaving the issue as it is.", file=sys.stderr)
        return 1
    if args.dry_run:
        title, body = render(args.sdk_version, failures, ran, args.run_url)
        print(f"{title}\n\n{body}" if failures else f"green: mcp {args.sdk_version}, {ran} tests")
        return 0
    print(sync_issue(gh, args.repo, args.sdk_version, failures, ran, args.run_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
