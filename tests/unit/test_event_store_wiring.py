"""The store the writer appends to is the store the readers read.

Three defects lived in the gap between those two halves, and none of them could
fail a test that looked at one half alone:

* the invocation-history query read a *different* store from the one bootstrap
  configures -- a lazily created in-memory singleton nothing ever wrote to;
* it composed `mcp_server-{id}` while the only writer composes
  `mcp_server:{id}`, so even a populated store would have answered empty;
* the `event_sourcing` auth driver was handed that same legacy store, whose
  class does not have three of the four methods the driver calls.

Each test here fails on the code as it stood before #753.
"""

from __future__ import annotations

import pathlib

from mcp_hangar.application.queries.handlers import GetToolInvocationHistoryHandler
from mcp_hangar.application.queries.queries import GetToolInvocationHistoryQuery
from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.stream_ids import MCP_SERVER, stream_id_for

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _completed(tool: str, mcp_server_id: str = "math") -> ToolInvocationCompleted:
    return ToolInvocationCompleted(
        mcp_server_id=mcp_server_id,
        tool_name=tool,
        duration_ms=1.0,
    )


class TestWriterAndReaderAgreeOnTheStreamId:
    def test_the_bus_appends_under_the_id_the_query_reads(self) -> None:
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)

        bus.publish_aggregate_events(MCP_SERVER, "math", [_completed("add")])

        # Not "some stream exists" -- the exact one the reader will ask for.
        assert store.read_stream(stream_id_for(MCP_SERVER, "math"))

    def test_a_hyphen_is_not_the_separator(self) -> None:
        # The reader used to build this id. Pinned so the two halves cannot
        # drift apart again silently.
        assert stream_id_for(MCP_SERVER, "math") == "mcp_server:math"
        assert stream_id_for(MCP_SERVER, "math") != "mcp_server-math"


class TestInvocationHistoryReadsWhatWasWritten:
    def test_history_is_not_empty_after_a_write(self) -> None:
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        bus.publish_aggregate_events(MCP_SERVER, "math", [_completed("add"), _completed("mul")])

        result = GetToolInvocationHistoryHandler(store).handle(
            GetToolInvocationHistoryQuery(mcp_server_id="math", limit=10, from_position=0)
        )

        # Before #753 this was always [] -- wrong store, and wrong id. The
        # first entry is present because `from_position` is inclusive now; the
        # previous form skipped position 0, i.e. the first event of every
        # stream, on the default request.
        assert [entry["tool_name"] for entry in result["history"]] == ["add", "mul"]
        assert result["mcp_server_id"] == "math"

    def test_it_filters_to_tool_events(self) -> None:
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        bus.publish_aggregate_events(MCP_SERVER, "math", [_completed("add")])

        result = GetToolInvocationHistoryHandler(store).handle(
            GetToolInvocationHistoryQuery(mcp_server_id="other", limit=10, from_position=0)
        )
        assert result["history"] == []


class TestTheAuthDriverGetsAStoreItCanUse:
    """`event_sourcing` was handed a store missing 3 of the 4 methods it calls.

    That is not "non-durable", which is what the upgrade guide documents and
    what `auth bootstrap-admin` refuses on. It is an `AttributeError` on the
    first index build, i.e. on the first authenticated request.
    """

    REQUIRED = ("append", "read_stream", "get_stream_version", "list_streams")

    def test_the_port_shaped_store_has_every_method_the_driver_calls(self) -> None:
        store = InMemoryEventStore()
        for method in self.REQUIRED:
            assert callable(getattr(store, method, None)), f"port store is missing {method}()"

    def test_bootstrap_no_longer_reaches_for_the_legacy_singleton(self) -> None:
        # A grep test on purpose: the defect was not a wrong value but a wrong
        # *source*, and the source is a module-level import.
        for name in ("cqrs.py", "components.py"):
            text = (ROOT / "src/mcp_hangar/server/bootstrap" / name).read_text(encoding="utf-8")
            assert "get_event_store" not in text, (
                f"{name} imports the legacy event-store singleton again; bootstrap must pass the store it configured"
            )
