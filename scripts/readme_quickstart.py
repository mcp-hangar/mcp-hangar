#!/usr/bin/env python3
"""Run the README quickstart's shell blocks literally, in order (#1774).

A fenced ``bash`` block that follows a ``<!-- ci:quickstart -->`` marker line is
part of the quickstart. They are written to one script and run with ``bash -e``
in an empty directory under a throwaway ``HOME``, so a step that only works on
the author's machine -- a binary that is on the author's PATH, a file left from
last week -- fails here instead of in front of a reader.

Blocks that cannot run on a runner (they wait for a restarted MCP client) carry
no marker and are not run.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = "<!-- ci:quickstart -->"
FENCE = re.compile(r"^```bash\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def quickstart_blocks(text: str) -> list[str]:
    blocks = []
    for match in FENCE.finditer(text):
        before = text[: match.start()].rstrip("\n").rsplit("\n", 1)[-1]
        if before.strip() == MARKER:
            blocks.append(match.group(1))
    return blocks


def main(argv: list[str]) -> int:
    readme = Path(argv[1] if len(argv) > 1 else "README.md").resolve()
    blocks = quickstart_blocks(readme.read_text(encoding="utf-8"))
    if not blocks:
        print(f"no {MARKER} block in {readme}: nothing to run", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        env = {**os.environ, "HOME": home}
        for number, block in enumerate(blocks, 1):
            print(f"--- quickstart block {number}/{len(blocks)} ---\n{block}", flush=True)
            result = subprocess.run(["bash", "-e", "-c", block], cwd=work, env=env, check=False)
            if result.returncode:
                print(f"block {number} exited {result.returncode}", file=sys.stderr)
                return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
