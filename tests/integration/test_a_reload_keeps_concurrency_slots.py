"""A reload keeps the concurrency slots the running calls hold (#1432).

Every reload built a new concurrency manager. A call already running held its
slots on the old one, so while it ran the new manager admitted a full limit's
worth of calls beside it: up to twice the configured number, on every reload,
a byte-identical one from the file watcher included. And a server's
`max_concurrency` was set on the running manager while the new configuration was
still being built, so a reload refused later had already changed it.

Through the real `ReloadConfigurationHandler` and `ServerConfigLoader`, on the
process's own runtime and on the manager the executor behind `hangar_call`
acquires its slots on. The calls are held on that manager's `acquire`, which is
what the executor enters around each invoke; the servers are never started.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.policies.header_exposure import clear_header_exposure_policies
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import ServerConfigLoader, load_configuration
from mcp_hangar.server.state import GROUPS, get_runtime
from mcp_hangar.server.tools import batch
from mcp_hangar.server.tools.batch.concurrency import (
    DEFAULT_PROVIDER_CONCURRENCY,
    ConcurrencyManager,
    get_concurrency_manager,
    reset_concurrency_manager,
)

IDS = ("keep", "change", "drop", "bad", "m1")


def _server(**extra: Any) -> dict[str, Any]:
    """A server that is never started: a reload only builds, stops and replaces it."""
    return {"mode": "subprocess", "command": ["python", "-c", "pass"], **extra}


def _config(servers: dict[str, Any], global_limit: int = 10) -> dict[str, Any]:
    return {"execution": {"max_concurrency": global_limit}, "mcp_servers": servers}


BOOTED = _config(
    {
        "keep": _server(max_concurrency=2),
        "change": _server(max_concurrency=2),
        "drop": _server(max_concurrency=2),
        "pool": {"mode": "group", "auto_start": False, "max_concurrency": 3, "members": [_server(id="m1")]},
    }
)
#: `keep` and the group as they were, `change` lowered to 1, `drop` deleted, and the global limit lowered.
EDITED = _config(
    {
        "keep": _server(max_concurrency=2),
        "change": _server(max_concurrency=1),
        "pool": {"mode": "group", "auto_start": False, "max_concurrency": 3, "members": [_server(id="m1")]},
    },
    global_limit=6,
)


def _reset() -> None:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    clear_header_exposure_policies()
    batch.configure_interceptors(None)
    reset_concurrency_manager()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in IDS:
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)
    GROUPS.clear()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    _reset()
    yield
    _reset()


class _Gateway:
    """A config file, the process state it was booted into, and the reload handler bootstrap wires."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handler = ReloadConfigurationHandler(
            get_runtime().repository, Mock(), str(path), config_loader=ServerConfigLoader(), groups=GROUPS
        )

    def boot(self, config: dict[str, Any]) -> None:
        self.path.write_text(yaml.safe_dump(config, sort_keys=False))
        load_configuration(str(self.path))

    def reload(self, config: dict[str, Any]) -> dict[str, Any]:
        self.path.write_text(yaml.safe_dump(config, sort_keys=False))
        return self.handler.handle(ReloadConfigurationCommand(requested_by="test"))


@pytest.fixture
def gateway(tmp_path: Path) -> _Gateway:
    return _Gateway(tmp_path / "config.yaml")


def _manager() -> ConcurrencyManager:
    """The manager a `hangar_call` acquires its slots on, read as the executor reads it on each call."""
    return batch.configured_executor().concurrency_manager


class _Calls:
    """Calls holding slots on the executor's manager, each until it is released."""

    def __init__(self) -> None:
        self._threads: list[threading.Thread] = []
        self._releases: list[threading.Event] = []

    def hold(self, mcp_server_id: str) -> threading.Event:
        entered, release = threading.Event(), threading.Event()
        self._start(mcp_server_id, entered, release)
        assert entered.wait(5), f"a call to {mcp_server_id} did not get its slot"
        return release

    def start(self, mcp_server_id: str) -> threading.Event:
        """A call that may have to wait for its slot; the event is set once it holds it."""
        entered = threading.Event()
        released = threading.Event()
        released.set()
        self._start(mcp_server_id, entered, released)
        return entered

    def _start(self, mcp_server_id: str, entered: threading.Event, release: threading.Event) -> None:
        manager = _manager()

        def run() -> None:
            with manager.acquire(mcp_server_id):
                entered.set()
                release.wait(10)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self._threads.append(thread)
        self._releases.append(release)

    def join(self) -> None:
        """Release every call still held, so a test that failed part-way leaves no thread behind."""
        for release in self._releases:
            release.set()
        for thread in self._threads:
            thread.join(5)
            assert not thread.is_alive()


@pytest.fixture
def calls() -> Iterator[_Calls]:
    held = _Calls()
    yield held
    held.join()


def _limits() -> dict[str | None, int]:
    manager = get_concurrency_manager()
    return {
        None: manager.global_limit,
        **{sid: manager.get_mcp_server_limit(sid) for sid in ("keep", "change", "drop", "pool")},
    }


def _in_flight() -> dict[str | None, int]:
    manager = get_concurrency_manager()
    return {sid: manager.in_flight(sid) for sid in (None, "keep", "change", "drop")}


class TestTheRunningCallsKeepTheirSlots:
    def test_a_reload_that_changes_keeps_and_removes_limits(self, gateway: _Gateway, calls: _Calls) -> None:
        gateway.boot(BOOTED)
        manager = _manager()
        assert _limits() == {None: 10, "keep": 2, "change": 2, "drop": 2, "pool": 3}
        releases = [calls.hold(sid) for sid in ("keep", "keep", "change", "change", "drop")]

        result = gateway.reload(EDITED)

        # Only the servers this test declares: the reload also removes any an
        # earlier test left in the process's repository.
        assert result["success"] and [sid for sid in result["mcp_servers_removed"] if sid in IDS] == ["drop"]
        assert get_concurrency_manager() is manager and _manager() is manager, "the same manager, not a new one"
        assert _limits() == {None: 6, "keep": 2, "change": 1, "drop": DEFAULT_PROVIDER_CONCURRENCY, "pool": 3}
        assert _in_flight() == {None: 5, "keep": 2, "change": 2, "drop": 1}

        # Counted against the slots the running calls hold: a new manager
        # would have let both of these in at once.
        to_keep, to_change = calls.start("keep"), calls.start("change")
        assert not to_keep.wait(0.2) and not to_change.wait(0.2)

        releases[0].set()  # one call to `keep`: its slot goes back to `keep`
        assert to_keep.wait(5)
        releases[2].set()  # one call to `change`, which still has one running at a limit of 1
        assert not to_change.wait(0.2)
        releases[3].set()
        assert to_change.wait(5)

        for release in releases:
            release.set()
        calls.join()
        assert _in_flight() == {None: 0, "keep": 0, "change": 0, "drop": 0}
        assert manager._limiters == {}, "the removed server's limiter went with its last call"

    def test_an_unchanged_file_leaves_the_manager_and_every_slot_count_as_it_was(
        self, gateway: _Gateway, calls: _Calls
    ) -> None:
        gateway.boot(BOOTED)
        manager = _manager()
        releases = [calls.hold("keep"), calls.hold("keep")]
        limiter = manager._limiters["keep"]

        gateway.reload(BOOTED)

        assert _manager() is manager and manager._limiters["keep"] is limiter
        assert _limits() == {None: 10, "keep": 2, "change": 2, "drop": 2, "pool": 3}
        assert _in_flight() == {None: 2, "keep": 2, "change": 0, "drop": 0}
        third = calls.start("keep")
        assert not third.wait(0.2), "the limit is 2, and two calls run"

        releases[0].set()
        assert third.wait(5)
        releases[1].set()


class TestARefusedReloadChangesNoLimit:
    def test_a_server_block_refused_after_a_limit_was_built(self, gateway: _Gateway, calls: _Calls) -> None:
        """`keep` is built with its new limit before `bad` is refused; the limit used to be set already."""
        gateway.boot(BOOTED)
        release = calls.hold("keep")
        broken = _server(access={"prompt": {"approval_list": ["draft_*"]}})

        with pytest.raises(ConfigurationError, match="approval_list"):
            gateway.reload(_config({"keep": _server(max_concurrency=5), "bad": broken}, global_limit=20))

        assert _limits() == {None: 10, "keep": 2, "change": 2, "drop": 2, "pool": 3}
        assert _in_flight() == {None: 1, "keep": 1, "change": 0, "drop": 0}
        release.set()

    @pytest.mark.parametrize(
        "config",
        [
            _config({"keep": _server(max_concurrency=2)}, global_limit=-1),
            _config({"keep": _server(max_concurrency=-1)}),
        ],
        ids=["global", "server"],
    )
    def test_a_negative_limit_is_refused_before_anything_changes(
        self, gateway: _Gateway, config: dict[str, Any]
    ) -> None:
        gateway.boot(BOOTED)
        running = get_runtime().repository.get("drop")

        with pytest.raises(ConfigurationError, match="must be 0 \\(no limit\\) or more"):
            gateway.reload(config)

        assert _limits() == {None: 10, "keep": 2, "change": 2, "drop": 2, "pool": 3}
        assert get_runtime().repository.get("drop") is running, "no server was stopped or removed"
