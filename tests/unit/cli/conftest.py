"""Keep the state `init` records inside the test, not in the developer's home."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def init_state_stays_in_the_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
