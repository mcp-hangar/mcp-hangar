"""A misspelled config key must be refused, not kept and ignored (#982).

The four cases below are the ones measured on the released loader: each was
accepted, and each produced a gateway that ran with the setting silently not
applied. `test_the_typo_that_used_to_boot_now_refuses` is the one that matters
-- it drives the real `load_config_from_file`, so it fails if the schema exists
but nothing calls it.
"""

from pathlib import Path

import pytest
import yaml

from mcp_hangar.server.config import load_config_from_file
from mcp_hangar.server.config_schema import SECTIONS, SERVER_SPEC_KEYS, ConfigSchemaError, validate_config

# Accepted by the loader before this gate; each is a setting that did not apply.
TYPOS = {
    "a misspelled server key": {"mcp_servers": {"m": {"mode": "subprocess", "commandd": ["python"]}}},
    "a digit for an l": {"mcp_servers": {"m": {"mode": "subprocess", "idle_tt1_s": 60}}},
    "a whole misspelled section": {"mcp_servers": {}, "authh": {"enabled": True}},
    # The expensive one: a deployment that believes it enabled authentication.
    "a misspelled key inside auth": {"mcp_servers": {}, "auth": {"enabledd": True}},
}


@pytest.mark.parametrize("case", TYPOS, ids=list(TYPOS))
def test_a_typo_is_reported(case):
    problems = validate_config(TYPOS[case])
    assert problems, f"{case} was accepted"


@pytest.mark.parametrize("case", TYPOS, ids=list(TYPOS))
def test_the_message_names_the_key_and_the_allowed_set(case):
    """`dsl.py` names both, which is what makes a schema error actionable."""
    problem = validate_config(TYPOS[case])[0]
    assert "unknown key" in problem
    assert "allowed keys:" in problem


def test_a_valid_config_is_accepted():
    config = {
        "mcp_servers": {"m": {"mode": "subprocess", "command": ["python"], "idle_ttl_s": 60}},
        "auth": {"enabled": True, "allow_anonymous": False},
        "rate_limit": {"rps": 20, "burst": 40},
    }
    assert validate_config(config) == []


def test_an_opaque_section_is_not_descended_into():
    """`truncation` is owned by `TruncationConfig.from_dict`, so guessing at its
    keys here would reject a valid config -- the failure this schema must not
    have."""
    assert SECTIONS["truncation"] is None
    assert validate_config({"mcp_servers": {}, "truncation": {"anything_at_all": 1}}) == []


def test_the_typo_that_used_to_boot_now_refuses(tmp_path, monkeypatch):
    """Drives the real loader, so it fails if nothing calls the schema."""
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"mcp_servers": {"m": {"mode": "subprocess", "commandd": ["python"]}}}))

    monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")
    with pytest.raises(ConfigSchemaError) as excinfo:
        load_config_from_file(str(config))
    assert "commandd" in str(excinfo.value)


def test_without_strict_mode_the_config_still_loads(tmp_path, monkeypatch):
    """Warn before reject: a stale key must not break an upgrade in 2.x."""
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"mcp_servers": {"m": {"mode": "subprocess", "commandd": ["python"]}}}))

    monkeypatch.delenv("HANGAR_CONFIG_STRICT", raising=False)
    loaded = load_config_from_file(str(config))
    assert "m" in loaded["mcp_servers"]


def test_every_shipped_example_config_passes():
    """The schema is derived from the readers, so a shipped config it rejects
    means the schema is short -- and a short schema refuses valid deployments."""
    examples = sorted(Path(__file__).resolve().parents[2].glob("examples/*/config*.y*ml"))
    assert examples, "no example configs found -- this test stopped checking anything"
    for path in examples:
        loaded = yaml.safe_load(path.read_text())
        if isinstance(loaded, dict) and "mcp_servers" in loaded:
            assert validate_config(loaded) == [], f"{path.name} rejected"


def test_the_schema_tracks_the_readers_not_itself():
    """#1005: `max_concurrency` has a reader and docs but warned as unknown;
    `working_dir` had a schema entry and no reader, so the schema blessed a
    key that silently did nothing."""
    read_and_documented = {"mcp_servers": {"m": {"mode": "subprocess", "command": ["python"], "max_concurrency": 5}}}
    assert validate_config(read_and_documented) == []
    assert validate_config({"mcp_servers": {"m": {"mode": "subprocess", "command": ["python"], "working_dir": "/x"}}})


def test_the_server_spec_key_set_did_not_silently_empty():
    assert len(SERVER_SPEC_KEYS) > 20
    assert "command" in SERVER_SPEC_KEYS and "mode" in SERVER_SPEC_KEYS
