"""Filesystem discovery refuses a definition file any local user could write.

A discovered definition names a command the gateway runs, so a world-writable
file, a file under a world-writable directory, and a symlink leading out of the
discovery directory are refused, each with one warning rather than one per scan.
Group-writable files and directories, and files owned by another user, are
still accepted.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_hangar.infrastructure.discovery import filesystem_source
from mcp_hangar.infrastructure.discovery.filesystem_source import FilesystemDiscoverySource

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(os.name == "nt", reason="POSIX modes and owners"),
]

DEFINITION = "name: {name}\nmode: subprocess\nconnection:\n  command: [python3, -m, some_server]\n"


def _write(path: Path, name: str, mode: int = 0o644) -> Path:
    path.write_text(DEFINITION.format(name=name))
    os.chmod(path, mode)
    return path


@pytest.fixture
def watched(tmp_path: Path) -> Path:
    directory = tmp_path / "mcp_servers.d"
    directory.mkdir()
    os.chmod(directory, 0o755)
    return directory


@pytest.fixture
def warnings(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(filesystem_source, "logger", fake)
    return fake


def _refusals(fake: MagicMock) -> list[dict]:
    return [c.kwargs for c in fake.warning.call_args_list if c.args == ("filesystem_discovery_file_refused",)]


async def _names(directory: Path) -> set[str]:
    source = FilesystemDiscoverySource(path=str(directory), watch=False)
    return {s.name for s in await source.discover()}


async def test_a_plain_file_owned_by_the_gateway_user_is_discovered(watched: Path, warnings: MagicMock) -> None:
    _write(watched / "ok.yaml", "ok")

    assert await _names(watched) == {"ok"}
    assert _refusals(warnings) == []


@pytest.mark.parametrize("mode", [0o666, 0o646])
async def test_a_world_writable_file_is_refused(watched: Path, warnings: MagicMock, mode: int) -> None:
    _write(watched / "ok.yaml", "ok")
    _write(watched / "ww.yaml", "writable", mode)

    assert await _names(watched) == {"ok"}
    [refusal] = _refusals(warnings)
    assert refusal["file_path"].endswith("ww.yaml")
    assert "writable" in refusal["reason"]


async def test_a_file_in_a_world_writable_directory_is_refused(watched: Path, warnings: MagicMock) -> None:
    _write(watched / "inside.yaml", "inside")
    os.chmod(watched, 0o777)
    try:
        assert await _names(watched) == set()
    finally:
        os.chmod(watched, 0o755)
    [refusal] = _refusals(warnings)
    assert "world-writable" in refusal["reason"]


async def test_a_symlink_leading_out_of_the_directory_is_refused(watched: Path, warnings: MagicMock) -> None:
    outside = watched.parent / "outside"
    outside.mkdir()
    target = _write(outside / "escaped.yaml", "escaped")
    (watched / "link.yaml").symlink_to(target)

    assert await _names(watched) == set()
    [refusal] = _refusals(warnings)
    assert "outside the discovery path" in refusal["reason"]


async def test_a_configmap_style_symlink_inside_the_directory_is_followed(watched: Path, warnings: MagicMock) -> None:
    # The layout kubelet gives a mounted ConfigMap: x.yaml -> ..data/x.yaml, ..data -> ..<timestamp>
    stamped = watched / "..2026_10_24_00_00_00.000000000"
    stamped.mkdir()
    os.chmod(stamped, 0o755)
    _write(stamped / "cm.yaml", "from-configmap")
    (watched / "..data").symlink_to(stamped.name)
    (watched / "cm.yaml").symlink_to("..data/cm.yaml")

    assert await _names(watched) == {"from-configmap"}
    assert _refusals(warnings) == []


async def test_a_refusal_warns_once_across_scans_and_again_after_the_file_returns(
    watched: Path, warnings: MagicMock
) -> None:
    bad = _write(watched / "ww.yaml", "writable", 0o666)
    source = FilesystemDiscoverySource(path=str(watched), watch=False)

    for _ in range(3):
        assert await source.discover() == []
    assert len(_refusals(warnings)) == 1

    bad.unlink()
    await source.discover()
    _write(watched / "ww.yaml", "writable", 0o666)
    await source.discover()
    assert len(_refusals(warnings)) == 2


async def test_a_fixed_file_is_discovered_on_the_next_scan(watched: Path, warnings: MagicMock) -> None:
    bad = _write(watched / "later.yaml", "later", 0o666)
    source = FilesystemDiscoverySource(path=str(watched), watch=False)
    assert await source.discover() == []

    os.chmod(bad, 0o644)
    assert [s.name for s in await source.discover()] == ["later"]


async def test_a_group_writable_file_or_directory_is_still_discovered(watched: Path, warnings: MagicMock) -> None:
    # umask 002, fsGroup volumes and shared group directories keep working.
    sub = watched / "team"
    sub.mkdir()
    os.chmod(sub, 0o775)
    _write(sub / "nested.yaml", "nested", 0o664)
    _write(watched / "top.yaml", "top", 0o664)
    source = FilesystemDiscoverySource(path=str(watched), pattern="**/*.yaml", watch=False)

    assert {s.name for s in await source.discover()} == {"top", "nested"}
    assert _refusals(warnings) == []


async def test_a_file_under_a_world_writable_subdirectory_is_refused(watched: Path, warnings: MagicMock) -> None:
    sub = watched / "drop"
    sub.mkdir()
    os.chmod(sub, 0o1777)
    _write(sub / "nested.yaml", "nested")
    _write(watched / "top.yaml", "top")
    source = FilesystemDiscoverySource(path=str(watched), pattern="**/*.yaml", watch=False)

    try:
        assert {s.name for s in await source.discover()} == {"top"}
    finally:
        os.chmod(sub, 0o755)
    [refusal] = _refusals(warnings)
    assert refusal["reason"].startswith(f"sits in {os.path.realpath(sub)}")


async def test_a_fifo_named_like_a_definition_is_refused_without_hanging(watched: Path, warnings: MagicMock) -> None:
    os.mkfifo(watched / "pipe.yaml", 0o644)
    _write(watched / "ok.yaml", "ok")

    assert await _names(watched) == {"ok"}
    [refusal] = _refusals(warnings)
    assert refusal["reason"] == "is not a regular file"
