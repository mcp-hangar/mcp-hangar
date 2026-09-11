"""The workload the tracing-overhead measurements run (#1292).

``hangar_call`` is driven through its real path -- validation, authorization,
``BatchExecutor`` with its worker pool and gates, ``CommandBus``,
``InvokeToolHandler``, the ``McpServer`` aggregate and a real ``StdioClient``
-- so every span Hangar opens per call is opened here, including the upstream
CLIENT span and the ``traceparent`` injected into ``_meta``. Only the upstream
process is replaced: ``_InProcessUpstream`` stands in for ``subprocess.Popen``
and answers each request from the calling thread, so there is no pipe,
subprocess or network in the number. What remains is Hangar's own cost plus one
thread hand-off to the client's reader thread, identical with tracing on or off.
"""

from __future__ import annotations

import json
import queue
from types import SimpleNamespace
from typing import Any

from mcp_hangar.application.commands.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.domain.model import McpServer, McpServerState
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.server.tools.batch import hangar_call
from mcp_hangar.stdio_client import StdioClient

SERVER_ID = "bench-upstream"
TOOL = "add"
_RESULT = {"content": [{"type": "text", "text": "3"}]}


class _InProcessUpstream:
    """A ``subprocess.Popen`` stand-in whose stdin answers every request at once."""

    pid = 0
    stderr = None

    def __init__(self) -> None:
        self._lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.stdin = self
        self.stdout = self

    def write(self, line: str) -> int:
        request = json.loads(line)
        if "id" in request:
            self._lines.put(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": _RESULT}) + "\n")
        return len(line)

    def flush(self) -> None:
        pass

    def readline(self) -> str:
        return self._lines.get()

    def poll(self) -> None:
        return None

    def eof(self) -> None:
        self._lines.put("")


class TracingWorkload:
    """One READY upstream behind the real call path, and a way to call it."""

    def __init__(self) -> None:
        self._upstream = _InProcessUpstream()
        self._client = StdioClient(self._upstream, mcp_server_id=SERVER_ID)  # type: ignore[arg-type]
        server = McpServer(mcp_server_id=SERVER_ID, mode="subprocess", command=["unused"])
        with server._lock:
            server._state = McpServerState.READY
            server._client = self._client
        server._tools.add(ToolSchema(name=TOOL, description="adds", input_schema={}))

        repository = InMemoryMcpServerRepository()
        repository.add(SERVER_ID, server)
        event_bus = EventBus()
        command_bus = CommandBus()
        command_bus.register(InvokeToolCommand, InvokeToolHandler(repository, event_bus))
        #: What ``mcp_hangar.server.context.get_context()`` must return while
        #: the workload runs: only the attributes the call path reads.
        self.ctx = SimpleNamespace(
            repository=repository,
            command_bus=command_bus,
            event_bus=event_bus,
            get_mcp_server=repository.get,
            mcp_server_exists=repository.exists,
        )

    def run(self, calls: int) -> dict[str, Any]:
        """One ``hangar_call`` carrying ``calls`` tool calls; every one must succeed."""
        batch = [{"mcp_server": SERVER_ID, "tool": TOOL, "arguments": {"a": 1, "b": 2}}] * calls
        response = hangar_call(calls=batch)
        if response.get("succeeded") != calls:
            raise RuntimeError(f"workload call failed: {response}")
        return response

    def close(self) -> None:
        self._client.closed = True
        self._upstream.eof()
