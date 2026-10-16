#!/usr/bin/env python3
"""Fuzz configuration documents: a configuration is checked or refused by name, never by a crash.

    python fuzz/fuzz_config_document.py -runs=1000000

The input is not decoded as JSON: it drives a structure-aware generator
(`build_config_document`) through `FuzzedDataProvider`, so every
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
    from invariants import check_config_document


def one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    check_config_document(lambda n: fdp.ConsumeIntInRange(0, n - 1))


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
