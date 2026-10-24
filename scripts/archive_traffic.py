#!/usr/bin/env python3
"""Archive GitHub traffic for the org's repositories, since GitHub keeps 14 days (#1797).

The `traffic` workflow runs this weekly in two steps, each in its own job:

* ``fetch`` reads the four traffic endpoints for every repository with the
  token in ``TRAFFIC_TOKEN`` and writes the raw responses under ``--out``.
  Nothing is written to git here, so the token never shares a job with
  ``contents: write``.
* ``merge`` folds those responses into the archive checkout (the ``traffic``
  branch), with no network access at all.

Archive layout, one directory per repository::

    <repo>/views.json                 daily entries, keyed by timestamp
    <repo>/clones.json                daily entries, keyed by timestamp
    <repo>/referrers/<YYYY-MM-DD>.json  the 14-day top-referrers snapshot fetched that day
    <repo>/paths/<YYYY-MM-DD>.json      the 14-day top-paths snapshot fetched that day

Re-runs deduplicate by date. A views/clones entry is keyed by its ``timestamp``
and the newest fetch wins, because the current day is partial and only grows. A
referrers/paths snapshot has no per-day breakdown, so it is stored under the date
it was fetched and a second run that day replaces it. Output is written with
sorted keys and a fixed indent, so an identical re-run is byte-identical and git
sees nothing to commit.

Usage:
    python scripts/archive_traffic.py fetch --owner mcp-hangar --repos a,b --out raw/
    python scripts/archive_traffic.py merge --raw raw/ --archive archive/ --date 2026-10-19
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API = "https://api.github.com"

#: Endpoint name -> path under /repos/{owner}/{repo}/.
ENDPOINTS: dict[str, str] = {
    "views": "traffic/views?per=day",
    "clones": "traffic/clones?per=day",
    "referrers": "traffic/popular/referrers",
    "paths": "traffic/popular/paths",
}

#: Endpoints whose daily entries are merged by timestamp, and the key holding them.
DAILY: dict[str, str] = {"views": "views", "clones": "clones"}
#: Endpoints stored as a dated snapshot.
SNAPSHOT = ("referrers", "paths")

#: A repository or owner name. It becomes a directory name, so nothing else is let through.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: (url, token) -> parsed JSON body. Raises urllib.error.HTTPError on a non-2xx answer.
Getter = Callable[[str, str], Any]


def _check_name(name: str) -> str:
    if not _NAME.match(name) or name in {".", ".."}:
        raise ValueError(f"not a repository name: {name!r}")
    return name


def _http_get(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mcp-hangar-traffic-archive",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 -- fixed https host
        return json.load(response)


def fetch(owner: str, repos: list[str], token: str, out: Path, get: Getter | None = None) -> list[str]:
    """Write ``out/<repo>/<endpoint>.json`` for every repository; return the failures.

    A repository that fails (no access, expired token) does not stop the others:
    what could be read is still written, and the caller fails the run afterwards.
    """
    get = get or _http_get
    _check_name(owner)
    failures: list[str] = []
    for repo in repos:
        _check_name(repo)
        for endpoint, suffix in ENDPOINTS.items():
            url = f"{API}/repos/{owner}/{repo}/{suffix}"
            try:
                body = get(url, token)
            except urllib.error.HTTPError as exc:
                failures.append(f"{owner}/{repo} {endpoint}: HTTP {exc.code}")
                continue
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                failures.append(f"{owner}/{repo} {endpoint}: {type(exc).__name__}")
                continue
            target = out / repo / f"{endpoint}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_json(target, body)
    return failures


def merge_daily(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union of two daily series keyed by ``timestamp``; on a clash the incoming entry wins."""
    by_timestamp: dict[str, dict[str, Any]] = {}
    for entry in [*existing, *incoming]:
        by_timestamp[str(entry["timestamp"])] = {
            "timestamp": entry["timestamp"],
            "count": entry["count"],
            "uniques": entry["uniques"],
        }
    return [by_timestamp[key] for key in sorted(by_timestamp)]


def merge(raw: Path, archive: Path, date: str) -> list[str]:
    """Fold every fetched response under ``raw`` into ``archive``; return the files written."""
    if not _DATE.match(date):
        raise ValueError(f"not a YYYY-MM-DD date: {date!r}")
    written: list[str] = []
    if not raw.is_dir():
        return written
    for repo_dir in sorted(p for p in raw.iterdir() if p.is_dir()):
        repo = _check_name(repo_dir.name)
        for endpoint, key in DAILY.items():
            source = repo_dir / f"{endpoint}.json"
            if not source.exists():
                continue
            incoming = json.loads(source.read_text()).get(key, [])
            target = archive / repo / f"{endpoint}.json"
            existing = json.loads(target.read_text()).get(key, []) if target.exists() else []
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_json(target, {key: merge_daily(existing, incoming)})
            written.append(str(target.relative_to(archive)))
        for endpoint in SNAPSHOT:
            source = repo_dir / f"{endpoint}.json"
            if not source.exists():
                continue
            target = archive / repo / endpoint / f"{date}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_json(target, json.loads(source.read_text()))
            written.append(str(target.relative_to(archive)))
    return written


def _write_json(target: Path, data: Any) -> None:
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="read the traffic API into a raw directory")
    p_fetch.add_argument("--owner", required=True)
    p_fetch.add_argument("--repos", required=True, help="comma-separated repository names")
    p_fetch.add_argument("--out", required=True, type=Path)

    p_merge = sub.add_parser("merge", help="fold a raw directory into the archive checkout")
    p_merge.add_argument("--raw", required=True, type=Path)
    p_merge.add_argument("--archive", required=True, type=Path)
    p_merge.add_argument("--date", default=datetime.now(UTC).strftime("%Y-%m-%d"))

    args = parser.parse_args(argv)

    if args.command == "fetch":
        token = os.environ.get("TRAFFIC_TOKEN", "")
        if not token:
            print("::error::TRAFFIC_TOKEN is empty; the workflow should have skipped before this step.")
            return 1
        repos = [r.strip() for r in args.repos.split(",") if r.strip()]
        failures = fetch(args.owner, repos, token, args.out)
        for failure in failures:
            print(f"::error::traffic fetch failed for {failure}")
        return 1 if failures else 0

    written = merge(args.raw, args.archive, args.date)
    print(f"merged {len(written)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
