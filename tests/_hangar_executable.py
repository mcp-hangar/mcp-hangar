"""The `mcp-hangar` a test launches is the one this checkout built (#1416).

The tests that drive the shipped CLI -- `tests/live` and the stdio integration
harness -- used to run the first `mcp-hangar` on `PATH`. On a machine with a
release installed globally (pipx, Homebrew, another venv) that is the release,
not the working tree: a fix looks broken, or a regression looks fixed, and
nothing says which binary ran.

So the lookup never reads `PATH`. The executable is the console script
installed beside the interpreter running the tests, and before a test first
uses it, the interpreter that script runs under is asked where it imports
`mcp_hangar` from. Anything but this checkout's `src/mcp_hangar` fails the test,
including a wheel of this checkout or an editable install of another one. A
missing script fails too, rather than skipping: a skip here is a green run that
tested nothing.
"""

from __future__ import annotations

import functools
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

CHECKOUT_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "mcp_hangar"

_SCRIPT = "mcp-hangar.exe" if sys.platform == "win32" else "mcp-hangar"

# The two shebangs an installer writes for a console script: the plain one, and
# the `/bin/sh` trampoline pip and uv fall back to when the interpreter's path is
# too long for a shebang or contains a space.
_TRAMPOLINE = re.compile(r"""^'''exec' (["'])(?P<interpreter>[^"']+)\1 "\$0" "\$@"$""", re.MULTILINE)

_PROBE = "import mcp_hangar; print(mcp_hangar.__file__)"


class HangarExecutableError(Exception):
    """The `mcp-hangar` beside the running interpreter is missing, or is not this checkout's."""


def locate(interpreter: str) -> Path:
    """Return the console script installed beside ``interpreter``, never one found on `PATH`.

    ``interpreter`` is deliberately not resolved: a venv's `python` is a symlink
    to the base interpreter, and following it would leave the venv whose `bin/`
    holds the script.
    """
    script = Path(interpreter).parent / _SCRIPT
    if not script.is_file():
        raise HangarExecutableError(
            f"no `{_SCRIPT}` beside the interpreter running the tests ({interpreter}); expected {script}. "
            "Install this checkout into that environment (`uv sync`, or `uv pip install -e .`). "
            "The first `mcp-hangar` on PATH is not used instead: it may be another install."
        )
    return script


def interpreter_of(script: Path) -> list[str]:
    """Return the command the console script runs under, read from its shebang."""
    if sys.platform == "win32":
        # A Windows console script is a launcher `.exe`, not text; the interpreter
        # it starts is the one installed beside it.
        return [str(script.with_name("python.exe"))]
    first, _, rest = script.read_bytes()[:4096].decode(errors="replace").partition("\n")
    if not first.startswith("#!"):
        raise HangarExecutableError(f"{script} has no shebang, so the interpreter it runs under is unknown")
    command = shlex.split(first[2:])
    if command != ["/bin/sh"]:
        return command
    match = _TRAMPOLINE.search(rest)
    if match is None:
        raise HangarExecutableError(f"{script} is a `/bin/sh` script that names no interpreter this helper can read")
    return [match["interpreter"]]


def imported_package(script: Path) -> Path:
    """Return the directory the interpreter behind ``script`` imports `mcp_hangar` from."""
    # Run from the script's own directory: `-c` puts the cwd first on sys.path,
    # and a console script gets its own directory there, so the probe sees the
    # same path the script does.
    result = subprocess.run(
        [*interpreter_of(script), "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(script.parent),
    )
    printed = result.stdout.strip().splitlines()
    if result.returncode != 0 or not printed:
        raise HangarExecutableError(
            f"the interpreter behind {script} could not import mcp_hangar:\n{result.stderr[-2000:]}"
        )
    return Path(printed[-1]).resolve().parent


@functools.cache
def _verified(interpreter: str) -> str:
    # Cached per interpreter, so the probe runs once per session. A failure is
    # not cached: every test that needs the executable fails with the message.
    script = locate(interpreter)
    package = imported_package(script)
    if package != CHECKOUT_PACKAGE:
        raise HangarExecutableError(
            f"{script} runs mcp_hangar from {package}, not from the checkout under test ({CHECKOUT_PACKAGE}). "
            "Install this checkout editable into the environment running the tests (`uv sync`, or "
            "`uv pip install -e .`)."
        )
    return str(script)


def hangar_executable() -> str:
    """Return this checkout's `mcp-hangar`, or fail the calling test saying why.

    The executable is the console script beside `sys.executable`, and its
    interpreter must import `mcp_hangar` from this checkout's `src/`. Checked
    once per session.
    """
    try:
        return _verified(sys.executable)
    except HangarExecutableError as exc:
        reason = str(exc)
    # Outside the `except`, so the report carries the message once rather than
    # again under "During handling of the above exception".
    pytest.fail(reason, pytrace=False)
