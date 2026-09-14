"""A group's circuit reset timeout did nothing, so a config that sets it is told (#1398).

`circuit_breaker.reset_timeout_s` was read into the group's breaker and never
consulted: the breaker half-opens only in `allow_request()`, which a group does
not call. It was removed rather than honoured, because an open group circuit
already closes once `min_healthy` members are back in rotation. It is now a key
nothing reads, reported under its own name: a warning by default, a refusal
under `HANGAR_CONFIG_STRICT` and from `mcp-hangar config check`.

The flat `circuit_reset_timeout_s` is the old `McpServerGroup` keyword. No
config reader ever took it, and it gets the same message.
"""

import re

import pytest
from structlog.testing import capture_logs
import yaml

from mcp_hangar.domain.model import McpServerGroup
from mcp_hangar.server.config import load_config, load_config_from_file
from mcp_hangar.server.config_schema import ConfigSchemaError, validate_config
from mcp_hangar.server.state import GROUPS, get_runtime

# Spelling -> the group spec keys that set it, and where the message puts it.
# Both keep a `failure_threshold` beside it, which must still apply.
SPELLINGS = {
    "nested": (
        {"circuit_breaker": {"failure_threshold": 5, "reset_timeout_s": 3600}},
        "circuit_breaker.reset_timeout_s",
    ),
    "flat": (
        {"circuit_breaker": {"failure_threshold": 5}, "circuit_reset_timeout_s": 3600},
        "circuit_reset_timeout_s",
    ),
}


def _config(**group_keys) -> dict:
    return {
        "mcp_servers": {
            "pool": {
                "mode": "group",
                "auto_start": False,  # no real subprocess
                "members": [{"id": "m1", "mode": "subprocess", "command": ["cmd1"]}],
                **group_keys,
            }
        }
    }


def _named(where: str) -> str:
    return f"mcp_servers.pool.{where} has no effect on group 'pool' and was removed"


@pytest.fixture(autouse=True)
def _isolated_registry():
    repository = get_runtime().repository
    original_servers = repository.get_all()
    original_groups = dict(GROUPS)

    yield

    repository.clear()
    for server_id, server in original_servers.items():
        repository.add(server_id, server)
    GROUPS.clear()
    GROUPS.update(original_groups)


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_the_message_names_the_group_and_says_the_key_does_nothing(spelling):
    keys, where = SPELLINGS[spelling]

    [problem] = validate_config(_config(**keys))

    assert problem.startswith(_named(where))
    # Named, not also reported as a typo with the allowed set.
    assert "unknown key" not in problem


def test_a_group_without_it_is_clean():
    assert validate_config(_config(circuit_breaker={"failure_threshold": 5})) == []


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_the_config_loads_with_a_warning_and_the_group_works(spelling, tmp_path, monkeypatch):
    monkeypatch.delenv("HANGAR_CONFIG_STRICT", raising=False)
    keys, where = SPELLINGS[spelling]
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(_config(**keys)))

    with capture_logs() as logs:
        loaded = load_config_from_file(str(config_file))
        load_config(loaded["mcp_servers"])

    warnings = [e["detail"] for e in logs if e["log_level"] == "warning" and e["event"] == "unknown_config_key"]
    assert len(warnings) == 1 and warnings[0].startswith(_named(where))

    group = GROUPS["pool"]
    assert group._circuit_breaker._config.failure_threshold == 5  # the rest of the block applies
    # Never started (auto_start off), so put it where a started member would be.
    group.get_member("m1").in_rotation = True
    selected = group.select_member()
    assert selected is not None and str(selected.id) == "m1"
    assert group.circuit_open is False


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_strict_mode_refuses_it_before_anything_loads(spelling, tmp_path, monkeypatch):
    monkeypatch.setenv("HANGAR_CONFIG_STRICT", "1")
    keys, where = SPELLINGS[spelling]
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(_config(**keys)))

    with pytest.raises(ConfigSchemaError, match=re.escape(_named(where))):
        load_config_from_file(str(config_file))


def test_the_group_no_longer_takes_the_keyword():
    with pytest.raises(TypeError, match="circuit_reset_timeout_s"):
        McpServerGroup(group_id="pool", circuit_reset_timeout_s=60.0)
