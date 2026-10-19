"""`mcp-hangar pin` binds pins to the upstream and `--check` notices a move (#1802).

Two servers serving the same tools: pinning one and copying its block to the
other, or renaming a server and pointing it elsewhere, used to carry the pins
across silently. The written block now names the upstream it was read from, and
a server that points somewhere else is drift, whatever its tools say.
"""

import sys
from pathlib import Path

import pytest
import yaml

from mcp_hangar.server.cli.services.pinning import (
    UPSTREAM_DRIFT,
    find_drift,
    merge_pins,
    observe_digests,
    write_config_with_pins,
)

MOCK_PROVIDER = str(Path(__file__).resolve().parent.parent / "mock_provider.py")

# `observe_digests` loads the file the way the CLI does, which commits its servers
# and pins process-wide; a pin left behind refuses its tool in a later test.
pytestmark = pytest.mark.usefixtures("applied_configuration_is_undone")


def _server(*extra: str) -> dict:
    return {"mode": "subprocess", "command": [sys.executable, MOCK_PROVIDER, *extra]}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"mcp_servers": {"first": _server()}}))
    return path


def _pinned(path: Path) -> dict:
    document = yaml.safe_load(path.read_text())
    write_config_with_pins(path, merge_pins(document, observe_digests(path)))
    return yaml.safe_load(path.read_text())


def test_the_written_block_names_the_upstream_it_was_read_from(config_path):
    written = _pinned(config_path)

    block = written["mcp_servers"]["first"]["tool_projection"]
    assert block["upstream"].startswith("stdio:")
    assert block["pins"]


def test_the_same_server_still_agrees(config_path):
    written = _pinned(config_path)

    assert find_drift(written, observe_digests(config_path)) == []


def test_renaming_a_server_without_moving_it_is_not_drift(config_path):
    written = _pinned(config_path)
    spec = written["mcp_servers"].pop("first")
    written["mcp_servers"]["renamed"] = spec

    assert find_drift(written, observe_digests(config_path_with(config_path, written))) == []


def test_a_block_copied_to_a_server_with_another_upstream_is_drift(config_path):
    """Approving one server does not approve the other, whatever tools it serves."""
    written = _pinned(config_path)
    copied = written["mcp_servers"]["first"]["tool_projection"]
    written["mcp_servers"]["second"] = {**_server("--other"), "tool_projection": dict(copied)}

    drift = find_drift(written, observe_digests(config_path_with(config_path, written)))

    assert [(d.mcp_server_id, d.tool) for d in drift] == [("second", UPSTREAM_DRIFT)]
    assert drift[0].expected != drift[0].observed


def test_a_renamed_server_pointed_elsewhere_does_not_keep_its_pins(config_path):
    written = _pinned(config_path)
    spec = written["mcp_servers"].pop("first")
    spec["command"] = [sys.executable, MOCK_PROVIDER, "--moved"]
    written["mcp_servers"]["renamed"] = spec

    drift = find_drift(written, observe_digests(config_path_with(config_path, written)))

    assert [(d.mcp_server_id, d.tool) for d in drift] == [("renamed", UPSTREAM_DRIFT)]


def test_pins_written_before_the_binding_existed_are_not_drift(config_path):
    written = _pinned(config_path)
    del written["mcp_servers"]["first"]["tool_projection"]["upstream"]

    assert find_drift(written, observe_digests(config_path_with(config_path, written))) == []


def config_path_with(path: Path, document: dict) -> Path:
    path.write_text(yaml.safe_dump(document))
    return path
