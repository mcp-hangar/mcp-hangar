"""`max_consecutive_failures` outside 1-100 refuses the config (#1665).

The range lived in `MaxConsecutiveFailures`, which the loader never called, so
0 and 500 both booted.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.config import _load_mcp_server_config


def _spec(**extra) -> dict:
    return {"mode": "subprocess", "command": ["srv"], **extra}


@pytest.mark.parametrize("value", [1, 3, 100])
def test_the_documented_range_loads(value):
    assert _load_mcp_server_config("s", _spec(max_consecutive_failures=value)) is not None


def test_the_default_still_loads():
    assert _load_mcp_server_config("s", _spec()) is not None


@pytest.mark.parametrize("value", [0, -1, 101, 500])
def test_out_of_range_refuses_the_config(value):
    with pytest.raises(ConfigurationError, match="max_consecutive_failures"):
        _load_mcp_server_config("s", _spec(max_consecutive_failures=value))


@pytest.mark.parametrize("value", ["3", 2.5, True, None])
def test_a_non_integer_refuses_the_config(value):
    with pytest.raises(ConfigurationError, match="must be an integer"):
        _load_mcp_server_config("s", _spec(max_consecutive_failures=value))
