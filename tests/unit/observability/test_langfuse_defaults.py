"""Guards the secure-by-default posture of Langfuse content export."""

from __future__ import annotations

from mcp_hangar.integrations.langfuse import LangfuseConfig
from mcp_hangar.server.bootstrap.observability import LangfuseBootstrapConfig


def test_langfuse_scrubbing_is_on_by_default() -> None:
    cfg = LangfuseConfig()
    assert cfg.scrub_inputs is True
    assert cfg.scrub_outputs is True


def test_bootstrap_langfuse_scrubbing_is_on_by_default() -> None:
    cfg = LangfuseBootstrapConfig()
    assert cfg.scrub_inputs is True
    assert cfg.scrub_outputs is True
