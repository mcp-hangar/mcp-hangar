"""Keep what a CLI command changes inside the test, not in the developer's home or the worker."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver


@pytest.fixture(autouse=True)
def init_state_stays_in_the_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture(autouse=True)
def a_loaded_config_stays_in_the_test() -> Iterator[None]:
    """A command such as `pin --check` loads the config in this process, and loading
    it sets the process-wide tool access mode. Run for real, the command is its own
    process; here it would leave the next test in this worker behind a front door
    that denies every caller without a tenant."""
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()
