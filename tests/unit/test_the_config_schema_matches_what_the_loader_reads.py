"""The known-keys schema and the loader agree on a server's keys and discovery's (#1664).

`config check` and `HANGAR_CONFIG_STRICT=1` refused `network_mode`, `user` and
`discovery.lifecycle`, which the loader reads, and the approvals block for any
channel but two. The schema is a hand-kept list beside the code that reads the
keys, so it drifted. `test_the_config_schema_knows_every_section_that_is_read`
holds the top-level sections; this holds the two nested sets that drifted.

Both directions, by reading the loader's source rather than listing keys a
second time:

* every literal key the loader reads off a server spec, or off the discovery
  block, is a key the schema knows;
* every key the schema knows is read somewhere in the package, except the ones
  listed in `ACCEPTED_BUT_UNREAD`, each with its reason. A key leaves that list
  when something reads it, and the second test fails until it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

from mcp_hangar.server.config_schema import SECTIONS, SERVER_SPEC_KEYS, validate_config

_SRC = Path(__file__).resolve().parents[2] / "src" / "mcp_hangar"

#: Known to the schema, read by nothing. `transport` is in configs written for
#: older releases and refusing it would break them; it does nothing.
ACCEPTED_BUT_UNREAD = {"transport": "accepted for old configs; no reader (#1664)"}


def _keys_read(path: Path, function: str, variable: str) -> set[str]:
    """Literal keys read off *variable* with ``.get``, ``[]``, ``.pop`` or ``in`` inside *function*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name != function:
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "pop")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == variable
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == variable
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                found.add(node.slice.value)
            elif (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and any(isinstance(c, ast.Name) and c.id == variable for c in node.comparators)
            ):
                found.add(node.left.value)
    return found


def test_every_server_key_the_loader_reads_is_known_to_the_schema():
    read = _keys_read(_SRC / "server" / "config.py", "_load_mcp_server_config", "spec_dict")
    assert len(read) >= 15, f"the scan stopped matching the loader: {sorted(read)}"
    assert read - SERVER_SPEC_KEYS == set(), (
        "the loader reads these server keys but `config check` and strict mode call them unknown: "
        f"{sorted(read - SERVER_SPEC_KEYS)}"
    )


def test_every_discovery_key_the_loader_reads_is_known_to_the_schema():
    path = _SRC / "application" / "discovery" / "discovery_orchestrator.py"
    read = _keys_read(path, "from_dict", "data")
    assert len(read) >= 4, f"the scan stopped matching the loader: {sorted(read)}"
    assert read - SECTIONS["discovery"] == set(), sorted(read - SECTIONS["discovery"])


def _package_text_outside_the_schema() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _SRC.rglob("*.py") if p.name != "config_schema.py")


def test_every_key_the_schema_knows_is_read_somewhere():
    text = _package_text_outside_the_schema()
    dead = {
        key
        for key in SERVER_SPEC_KEYS | SECTIONS["discovery"]
        if f'"{key}"' not in text and f"'{key}'" not in text and key not in ACCEPTED_BUT_UNREAD
    }
    assert not dead, f"the schema accepts these keys and nothing reads them: {sorted(dead)}"


def test_an_exemption_goes_when_the_loader_reads_the_key():
    loader = _keys_read(_SRC / "server" / "config.py", "_load_mcp_server_config", "spec_dict")
    read = set(ACCEPTED_BUT_UNREAD) & loader
    assert not read, f"the loader reads {sorted(read)}: drop it from ACCEPTED_BUT_UNREAD"


def test_the_reported_keys_pass_strict_validation():
    config = {
        "mcp_servers": {"a": {"mode": "docker", "image": "x:1", "network_mode": "bridge", "user": "current"}},
        "discovery": {"lifecycle": {"default_ttl_s": 90}},
    }
    assert validate_config(config) == []


def test_the_channel_an_approvals_block_selects_may_carry_its_settings():
    assert validate_config({"approvals": {"channel": "email", "email": {"to": "ops@example.org"}}}) == []
    problems = validate_config({"approvals": {"channel": "event_stream", "email": {}}})
    assert problems and "email" in problems[0]
