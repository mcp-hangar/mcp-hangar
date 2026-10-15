"""Every Makefile target is runnable: its rule resolves and the paths it names exist (#1706).

`check-boundary` called a script that was removed, the `workspace-*` targets
called one that never shipped here, `proto-*` and `lint` needed an `api/`
directory that is not in this repository (so `make lint` failed on any machine),
and `make dev` read a config file that does not exist. Nothing ran them, so
nothing noticed.

`make -n` resolves each target without running its recipe; the paths a recipe
names are checked on top of that, since a dry run does not look at them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
_TARGET = re.compile(r"^([a-zA-Z_0-9-]+):(?!=)", re.MULTILINE)
_PATH_IN_RECIPE = re.compile(r"(?:(?<=\s)|^)((?:\./)?scripts/[\w./-]+)|cd\s+([\w./-]+)")

TARGETS = sorted(set(_TARGET.findall(MAKEFILE.read_text(encoding="utf-8"))) - {"help"})

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def test_there_are_targets_to_check() -> None:
    assert len(TARGETS) > 10, TARGETS


@needs_make
@pytest.mark.parametrize("target", TARGETS)
def test_the_target_resolves(target: str) -> None:
    result = subprocess.run(
        ["make", "-n", target, "VERSION=1.0.0"], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )

    assert result.returncode == 0, result.stderr


def test_every_script_and_directory_a_recipe_names_exists() -> None:
    missing = []
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("\t"):
            continue
        for script, directory in _PATH_IN_RECIPE.findall(line):
            named = script or directory
            if not (ROOT / named).exists():
                missing.append(f"{named}  ({line.strip()})")
    assert not missing, missing


def test_the_default_config_for_make_dev_is_none_or_a_file() -> None:
    match = re.search(r"^CONFIG \?=[ \t]*(\S*)", MAKEFILE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None
    assert match.group(1) == "" or (ROOT / match.group(1)).is_file()
