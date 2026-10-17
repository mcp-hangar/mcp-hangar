"""A `tool_projection` block that cannot be applied refuses the configuration (#1733).

`digest_enforcement` is the mode a pin mismatch is handled in, and a block that
is not a mapping is a typo for the same block. Both used to be dropped, one with
a log line, so a gateway ran on the default mode while its file said otherwise.
Refusing at load, with the key named, is the same rule `new_tools` and `upstream`
already follow.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.domain.value_objects.tool_digest import DigestEnforcement
from mcp_hangar.server import config as config_module
from mcp_hangar.server.config import ConfigurationError, _register_tool_projection_block


def _register(monkeypatch, block) -> ToolProjectionRegistry:
    registry = ToolProjectionRegistry()
    monkeypatch.setattr(config_module, "_staged_config", lambda: type("S", (), {"projections": registry})())
    _register_tool_projection_block("srv", block)
    return registry


class TestDigestEnforcement:
    @pytest.mark.parametrize("bad", ["strict", "BLOCK", "", 3, True, ["block"]])
    def test_a_value_that_is_not_a_mode_is_refused_by_name(self, bad, monkeypatch):
        with pytest.raises(ConfigurationError, match=r"tool_projection\.digest_enforcement for 'srv'"):
            _register(monkeypatch, {"digest_enforcement": bad})

    @pytest.mark.parametrize("mode", [mode.value for mode in DigestEnforcement])
    def test_every_real_mode_still_loads(self, mode, monkeypatch):
        registry = _register(monkeypatch, {"digest_enforcement": mode})

        assert registry.digest_enforcement("srv") == DigestEnforcement(mode)


class TestTheBlockItself:
    @pytest.mark.parametrize("bad", ["block", ["pins"], 5, True])
    def test_a_block_that_is_not_a_mapping_is_refused(self, bad, monkeypatch):
        with pytest.raises(ConfigurationError, match=r"Invalid tool_projection for 'srv'"):
            _register(monkeypatch, bad)

    def test_no_block_is_fine(self, monkeypatch):
        _register(monkeypatch, None)
