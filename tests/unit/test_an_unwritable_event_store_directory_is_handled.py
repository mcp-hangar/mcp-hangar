"""An existing directory the process cannot write into is handled like a missing one (#1694).

SQLite raises `sqlite3.OperationalError` ("unable to open database file") for it,
not `OSError`, so the handler that honours `allow_memory_fallback` and names the
path never ran: startup died with a bare traceback either way. This is the stock
image under `--read-only`, which creates `/app/data` and then cannot write there.

The existing tests in `test_event_store_durability.py` raise `OSError` from a
stub, which is the one type SQLite does not raise. These use a real directory.
"""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.observability.health import get_event_store_durability_status, set_event_store_durability_status
from mcp_hangar.server.bootstrap.event_store import EventStoreConfigurationError, init_event_store

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root can write into a mode 0500 directory")


class _Bus:
    def __init__(self) -> None:
        self.store = None

    def set_event_store(self, store) -> None:
        self.store = store

    def set_dispatch_checkpoint(self, checkpoint) -> None:
        pass


@pytest.fixture
def read_only_dir(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    yield directory
    directory.chmod(stat.S_IRWXU)


@pytest.fixture(autouse=True)
def _clean_health():
    set_event_store_durability_status(None)
    yield
    set_event_store_durability_status(None)


def _config(directory, *, fallback: bool) -> dict:
    return {
        "event_store": {
            "enabled": True,
            "driver": "sqlite",
            "path": str(directory / "events.db"),
            "allow_memory_fallback": fallback,
        }
    }


def test_without_the_fallback_startup_fails_with_a_message_naming_the_path(read_only_dir):
    runtime = SimpleNamespace(event_bus=_Bus())

    with pytest.raises(EventStoreConfigurationError) as exc:
        init_event_store(runtime, _config(read_only_dir, fallback=False))

    assert str(read_only_dir / "events.db") in str(exc.value)
    assert "allow_memory_fallback" in str(exc.value)


def test_with_the_fallback_it_starts_on_memory_and_says_it_is_degraded(read_only_dir):
    runtime = SimpleNamespace(event_bus=_Bus())

    init_event_store(runtime, _config(read_only_dir, fallback=True))

    assert isinstance(runtime.event_bus.store, InMemoryEventStore)
    status = get_event_store_durability_status()
    assert status.degraded is True and status.durable is False
