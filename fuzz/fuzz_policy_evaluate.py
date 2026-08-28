#!/usr/bin/env python3
"""Fuzz `egress_l7.evaluate`: every tool call ends in a verdict, in bounded time.

    python fuzz/fuzz_policy_evaluate.py -runs=1000000 fuzz/corpus/evaluate

The structure of the arguments is built from the input bytes rather than being
a flat string. That matters: the first real finding on this surface (#1102) was
`RecursionError` from deep nesting, which a flat-string fuzzer never reaches.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import atheris

sys.path.insert(0, str(Path(__file__).parent))

with atheris.instrument_imports():
    from invariants import check_evaluate

#: Groups a policy may name. A wrong one is rejected by `from_dict`, which is a
#: declared outcome, so the fuzzer would spend its budget on rejected policies.
SECRET_GROUPS = ("aws-keys", "jwt", "pem-blocks", "github-tokens", "bearer-tokens")


def _build_arguments(fdp: atheris.FuzzedDataProvider, depth: int = 0) -> Any:
    """Build a value whose SHAPE comes from the input, not only its bytes."""
    if depth > 6 or fdp.remaining_bytes() < 2:
        return fdp.ConsumeUnicodeNoSurrogates(64)

    match fdp.ConsumeIntInRange(0, 5):
        case 0:
            return fdp.ConsumeUnicodeNoSurrogates(256)
        case 1:
            return fdp.ConsumeInt(8)
        case 2:
            return {
                fdp.ConsumeUnicodeNoSurrogates(16): _build_arguments(fdp, depth + 1)
                for _ in range(fdp.ConsumeIntInRange(0, 4))
            }
        case 3:
            return [_build_arguments(fdp, depth + 1) for _ in range(fdp.ConsumeIntInRange(0, 4))]
        case 4:
            # A deliberate deep chain: the shape that found #1102. The fuzzer
            # will not stumble onto a thousand levels of nesting by mutation.
            return _chain(fdp.ConsumeIntInRange(0, 4000))
        case _:
            return None


def _chain(depth: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    cursor = root
    for _ in range(depth):
        cursor["a"] = {}
        cursor = cursor["a"]
    return root


def _build_policy(fdp: atheris.FuzzedDataProvider) -> dict[str, Any]:
    return {
        "mode": "audit" if fdp.ConsumeBool() else "enforce",
        "defaultAction": "Allow" if fdp.ConsumeBool() else "Deny",
        "tools": {
            "allow": [fdp.ConsumeUnicodeNoSurrogates(24) for _ in range(fdp.ConsumeIntInRange(0, 3))],
            "deny": [fdp.ConsumeUnicodeNoSurrogates(24) for _ in range(fdp.ConsumeIntInRange(0, 3))],
            "requireApproval": [fdp.ConsumeUnicodeNoSurrogates(24) for _ in range(fdp.ConsumeIntInRange(0, 2))],
        },
        "arguments": {
            "secretPatterns": [SECRET_GROUPS[fdp.ConsumeIntInRange(0, len(SECRET_GROUPS) - 1)]]
            if fdp.ConsumeBool()
            else [],
            "maxPayloadBytes": fdp.ConsumeIntInRange(0, 1_000_000) if fdp.ConsumeBool() else None,
        },
    }


def one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    tool_name = fdp.ConsumeUnicodeNoSurrogates(48)
    policy = _build_policy(fdp)
    headers = {
        "mcp-protocol-version": "2026-07-28" if fdp.ConsumeBool() else "2025-06-18",
        "mcp-param-region": fdp.ConsumeUnicodeNoSurrogates(16),
    }
    check_evaluate(tool_name, _build_arguments(fdp), policy, headers)


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
