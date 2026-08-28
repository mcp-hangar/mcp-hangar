#!/usr/bin/env python3
"""Fuzz tool-access precedence: deny wins, and keeps winning after a merge.

    python fuzz/fuzz_access_precedence.py -runs=1000000

The one target here whose failure is a policy BYPASS rather than a crash, so it
is worth saying what is being searched for: a (tool name, pattern sets) pair
where a name matching a deny list comes back allowed -- directly, or after
`merge()`, or through the composite policy a merge returns.

Patterns are drawn from a small alphabet including `*`, `?` and `[`, because
`fnmatch` metacharacters are where the interesting disagreements live; random
unicode would almost never produce a match at all, in either direction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).parent))

with atheris.instrument_imports():
    from invariants import check_access_precedence

#: Small on purpose: matches have to actually happen for the check to mean
#: anything, and `*?[]-` are the characters that make `fnmatch` interesting.
ALPHABET = "ab_*?[]-."


def _word(fdp: atheris.FuzzedDataProvider, limit: int = 8) -> str:
    length = fdp.ConsumeIntInRange(0, limit)
    return "".join(ALPHABET[fdp.ConsumeIntInRange(0, len(ALPHABET) - 1)] for _ in range(length))


def _patterns(fdp: atheris.FuzzedDataProvider) -> list[str]:
    return [_word(fdp) for _ in range(fdp.ConsumeIntInRange(0, 3))]


def one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    check_access_precedence(
        tool_name=_word(fdp, 12),
        allow=_patterns(fdp),
        deny=_patterns(fdp),
        approval=_patterns(fdp),
        other_allow=_patterns(fdp),
        other_deny=_patterns(fdp),
    )


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
