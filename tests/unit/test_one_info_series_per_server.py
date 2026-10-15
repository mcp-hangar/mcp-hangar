"""`mcp_hangar_mcp_server_info` has one series per server (#1700).

`update_mcp_server_state` defaulted `mode` to "subprocess", and every state event
that did not know the mode (a stop, a degrade, a state change) wrote that default
beside the real one, so a remote server exported both `mode="remote"` and
`mode="subprocess"`.
"""

from __future__ import annotations

import pytest

from mcp_hangar import metrics


@pytest.fixture(autouse=True)
def _clean():
    metrics.remove_mcp_server_series("remote-one")
    yield
    metrics.remove_mcp_server_series("remote-one")


def _modes() -> list[str]:
    return sorted(
        s.labels["mode"] for s in metrics.PROVIDER_INFO.collect() if s.labels.get("mcp_server") == "remote-one"
    )


def test_state_events_without_a_mode_do_not_add_a_second_series():
    metrics.update_mcp_server_state("remote-one", "ready", mode="remote")
    metrics.update_mcp_server_state("remote-one", "degraded")
    metrics.update_mcp_server_state("remote-one", "cold")

    assert _modes() == ["remote"]


def test_a_mode_replaces_the_series_instead_of_adding_one():
    metrics.update_mcp_server_state("remote-one", "cold")
    metrics.update_mcp_server_state("remote-one", "ready", mode="remote")

    assert _modes() == ["remote"]


def test_a_server_is_never_left_without_an_info_series_by_a_state_event():
    metrics.update_mcp_server_state("remote-one", "cold")

    assert len(_modes()) == 1


def test_a_changed_mode_leaves_only_the_new_one():
    metrics.update_mcp_server_state("remote-one", "ready", mode="subprocess")
    metrics.update_mcp_server_state("remote-one", "ready", mode="remote")

    assert _modes() == ["remote"]
