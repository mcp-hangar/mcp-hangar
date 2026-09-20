"""The tests that drive the shipped CLI launch this checkout's build, whatever PATH says (#1416).

`tests/live` and the stdio integration harness used to take the first
`mcp-hangar` on `PATH`, so a machine with a release installed globally tested
that release instead of the working tree, silently. The stubs below stand in for
that other install: one earlier on `PATH`, one installed where the helper looks
but built from somewhere else.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests._hangar_executable import CHECKOUT_PACKAGE, hangar_executable, imported_package

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stubs are POSIX shell scripts")


def _executable(file: Path, body: str) -> Path:
    file.write_text(body)
    file.chmod(file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return file


@pytest.fixture
def stub_first_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An `mcp-hangar` that exits 99, first on PATH: another install, as far as a PATH lookup can tell."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    stub = _executable(shadow / "mcp-hangar", "#!/bin/sh\nexit 99\n")
    monkeypatch.setenv("PATH", f"{shadow}{os.pathsep}{os.environ.get('PATH', '')}")
    return stub


def _fake_environment(root: Path, *, imports_from: Path, trampoline: bool = False) -> Path:
    """An environment whose `python` claims to import mcp_hangar from ``imports_from``; return that python."""
    bindir = root / "bin"
    bindir.mkdir(parents=True)
    python = _executable(bindir / "python", f"#!/bin/sh\necho '{imports_from / '__init__.py'}'\n")
    if trampoline:
        shebang = f"#!/bin/sh\n'''exec' '{python}' \"$0\" \"$@\"\n' '''\n"
    else:
        shebang = f"#!{python}\n"
    _executable(bindir / "mcp-hangar", shebang + "from mcp_hangar.server.cli import cli_main\n")
    return python


def test_a_stub_first_on_path_does_not_replace_the_working_trees_build(stub_first_on_path: Path):
    # The lookup the live tests used to make lands on the stub.
    assert shutil.which("mcp-hangar") == str(stub_first_on_path)

    resolved = Path(hangar_executable())

    assert resolved == Path(sys.executable).parent / "mcp-hangar"
    assert resolved != stub_first_on_path
    # It is this checkout's build: its interpreter imports the working tree...
    assert imported_package(resolved) == CHECKOUT_PACKAGE
    # ...and it runs, where the stub would exit 99.
    ran = subprocess.run([str(resolved), "--version"], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_a_missing_binary_fails_rather_than_falling_back_to_path(
    stub_first_on_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # An interpreter whose environment has no console script installed. A
    # fallback to PATH would return the stub instead of failing.
    bare = tmp_path / "bare-env" / "bin"
    bare.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(bare / "python"))

    with pytest.raises(pytest.fail.Exception, match=r"no `mcp-hangar` beside the interpreter running the tests"):
        hangar_executable()


def test_a_build_of_another_checkout_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Installed exactly where the helper looks, but importing another mcp_hangar:
    # a release, or another worktree's editable install.
    python = _fake_environment(tmp_path / "other-env", imports_from=tmp_path / "site-packages" / "mcp_hangar")
    monkeypatch.setattr(sys, "executable", str(python))

    with pytest.raises(pytest.fail.Exception, match=r"not from the checkout under test"):
        hangar_executable()


def test_a_trampoline_shebang_is_followed_to_its_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # pip and uv write `#!/bin/sh` plus an `exec` line when the interpreter's path
    # holds a space or is too long for a shebang.
    python = _fake_environment(tmp_path / "env with space", imports_from=CHECKOUT_PACKAGE, trampoline=True)
    monkeypatch.setattr(sys, "executable", str(python))

    assert hangar_executable() == str(python.parent / "mcp-hangar")
