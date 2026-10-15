#!/usr/bin/env python3
"""Check that every package `init` and `add` can write still resolves and is maintained (#1663).

Asks npm or PyPI for each registry entry and fails on a missing package or one
marked deprecated. The unit test beside the registry cannot reach a network;
this does, on a schedule (`.github/workflows/registry-packages.yml`).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from mcp_hangar.server.cli.services import get_all_mcp_servers


def _get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 -- fixed https registries
            return json.loads(response.read().decode(), strict=False)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _problem(install_type: str, package: str) -> str | None:
    if install_type == "uvx":
        data = _get(f"https://pypi.org/pypi/{urllib.parse.quote(package)}/json")
        if data is None:
            return "not on PyPI"
        if not (data["info"].get("author") or data["info"].get("author_email")):
            return "on PyPI with no author"
        return None
    data = _get(f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@')}")
    if data is None:
        return "not on npm"
    latest = data["dist-tags"]["latest"]
    deprecated = data["versions"][latest].get("deprecated")
    return f"deprecated on npm: {deprecated}" if deprecated else None


def main() -> int:
    failures = []
    for entry in get_all_mcp_servers():
        problem = _problem(entry.install_type, entry.package)
        print(f"{'FAIL' if problem else 'ok  '} {entry.name}: {entry.package}" + (f" ({problem})" if problem else ""))
        if problem:
            failures.append(entry.name)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
