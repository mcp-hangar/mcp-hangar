#!/usr/bin/env python3
"""Fuzz the policy parsers: a malformed policy is rejected, never a crash.

    python fuzz/fuzz_policy_parse.py -runs=1000000 fuzz/corpus/parse

Both `L7Policy.from_dict` and `dsl.parse_policy` read operator-authored CRD
payloads and document `ValueError` for anything malformed. Any other exception
means an operator can break the gateway's configuration path with a typo.

The input is decoded as JSON rather than mutated into a dict directly, because
that is how a policy actually arrives -- through a JSON/YAML decoder -- and a
fuzzer that skips the decoder tests a surface nothing reaches.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).parent))

with atheris.instrument_imports():
    from invariants import check_policy_parse


def one_input(data: bytes) -> None:
    try:
        decoded = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return  # Not a policy; the decoder's rejection is not our surface.
    check_policy_parse(decoded)


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
