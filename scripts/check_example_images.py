#!/usr/bin/env python3
"""Check that every image the examples reference resolves (#1681).

`examples/kubernetes` named `ghcr.io/modelcontextprotocol/mcp-sqlite`, `mcp-github`
and `mcp-postgres`, none of which can be pulled, and nothing noticed. This reads
every `image:` under `examples/` and asks the registry for its manifest.

An image that is not meant to resolve is listed in `NOT_PULLED` with the reason,
so a placeholder is a decision written down and an unlisted one is a failure.

    python scripts/check_example_images.py          # asks the registries
    python scripts/check_example_images.py --list   # prints what it would ask
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_LINE = re.compile(r"^\s*-?\s*image:\s*(?P<image>[^\s#]+)")

#: Image -> why it is not pulled.
NOT_PULLED = {
    "my-mcp-provider:latest": "placeholder in examples/discovery/kubernetes-deployment.yaml: the reader's own server",
    "mcp-hangar:latest": "built locally by the reader in examples/discovery/kubernetes-deployment.yaml",
    "ghcr.io/team-alpha/mcp-tools:latest": "placeholder for a team's own image in examples/kubernetes/discovery-source.yaml",
}


def referenced_images(root: Path = ROOT) -> dict[str, list[str]]:
    """Image -> the example files that name it. Variables and `localhost/` images are not included."""
    found: dict[str, list[str]] = {}
    for path in sorted((root / "examples").rglob("*")):
        if path.suffix not in (".yaml", ".yml") or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = IMAGE_LINE.match(line)
            if not match:
                continue
            image = match.group("image").strip("\"'")
            if "$" in image or image.startswith("localhost/"):
                continue
            rel = str(path.relative_to(root))
            if rel not in found.setdefault(image, []):
                found[image].append(rel)
    return found


def resolves(image: str) -> bool:
    result = subprocess.run(["docker", "manifest", "inspect", image], capture_output=True, text=True, timeout=120)
    return result.returncode == 0


def main(argv: list[str]) -> int:
    images = referenced_images()
    if "--list" in argv:
        for image, files in images.items():
            print(f"{image}\t{', '.join(files)}\t{'NOT PULLED: ' + NOT_PULLED[image] if image in NOT_PULLED else ''}")
        return 0
    failures = []
    for image, files in images.items():
        if image in NOT_PULLED:
            continue
        ok = resolves(image)
        print(f"{'ok  ' if ok else 'FAIL'} {image}  ({', '.join(files)})")
        if not ok:
            failures.append(image)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
