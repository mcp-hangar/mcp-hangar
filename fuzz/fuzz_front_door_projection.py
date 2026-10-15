#!/usr/bin/env python3
"""Fuzz the front door's projection: what the front door lists a caller is allowed and not withdrawn.

    python fuzz/fuzz_front_door_projection.py -runs=1000000

The input is not decoded as JSON: it drives a structure-aware generator
(`the fleet builder in invariants.py`) through `FuzzedDataProvider`, so every
byte decides a real choice -- which server, which tool, which tenant, which
block is the wrong kind of value -- instead of being rejected by a decoder
before it reaches the code under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).parent))

with atheris.instrument_imports():
    from invariants import check_front_door_projection


def one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    check_front_door_projection(lambda n: fdp.ConsumeIntInRange(0, n - 1))


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
