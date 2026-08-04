"""The protocol layer's view of the relay must match whether it is serving.

`protocol.forwardable_client_capabilities()` decides whether Hangar may forward
a caller's Tasks declaration upstream. It must not claim the extension when the
governed relay is off: an upstream would then mint a task for a client Hangar
cannot answer, and that client is left holding a handle it cannot use.

It used to answer the question by importing `server.context` and reading
`ctx.governed_task_store` -- a leaf protocol module reaching three layers up
into delivery for application state, and the biggest single jump in the import
contract's debt ledger.

Replacing that with a flag creates the obvious hazard: two sources of truth that
can disagree. The mitigation is that only one place in the codebase activates
the relay, and it writes both in the same statement group. These tests hold that
mitigation in place -- the flag tracking reality is the whole reason the
indirection is safe, so it is the thing worth testing, not the getter.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import Mock

import pytest

import mcp_hangar.protocol as protocol_module
from mcp_hangar.protocol import is_task_relay_wired, set_task_relay_wired


@pytest.fixture(autouse=True)
def _restore_flag():
    previous = is_task_relay_wired()
    yield
    set_task_relay_wired(previous)


class TestOnlyOnePlaceActivatesTheRelay:
    """The flag is safe only while `ctx.governed_task_store` has a single writer.

    If a second module starts assigning it, that module will not set the flag,
    and the protocol layer will quietly disagree with reality -- claiming the
    extension while nothing serves it, or the reverse.
    """

    def test_the_store_is_assigned_in_exactly_one_module(self):
        src = pathlib.Path(protocol_module.__file__).parent
        writers = []
        for path in src.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                    if isinstance(node, ast.AnnAssign)
                    else []
                )
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "governed_task_store":
                        writers.append(str(path.relative_to(src)))
        assert writers == ["fastmcp_server/task_relay_wiring.py"], (
            f"ctx.governed_task_store is assigned in more than one place: {sorted(set(writers))}. "
            "Each writer must also call set_task_relay_wired(), or the protocol layer "
            "will claim the Tasks extension while nothing serves it."
        )

    def test_that_module_sets_the_flag_on_both_paths(self):
        """Enabled and disabled both write it, so it is never left stale."""
        source = (pathlib.Path(protocol_module.__file__).parent / "fastmcp_server" / "task_relay_wiring.py").read_text(
            encoding="utf-8"
        )
        assert source.count("set_task_relay_wired(") >= 2, (
            "the wiring seam must set the flag on the enabled AND the disabled path"
        )


class TestTheWiringSeamKeepsThemInStep:
    def test_the_disabled_path_clears_the_flag(self):
        from mcp_hangar.fastmcp_server.task_relay_wiring import enable_governed_task_relay

        set_task_relay_wired(True)
        enable_governed_task_relay(Mock(), relay_tasks_enabled=False)

        assert is_task_relay_wired() is False

    def test_a_capability_is_not_claimed_after_the_disabled_path(self):
        """The failure this prevents, stated end to end."""
        from mcp_hangar.fastmcp_server.task_relay_wiring import enable_governed_task_relay
        from mcp_hangar.negotiation import ProtocolNegotiation, set_current_protocol_negotiation
        from mcp_hangar.protocol import TASKS_EXTENSION_ID, forwardable_client_capabilities

        set_current_protocol_negotiation(
            ProtocolNegotiation(
                protocol_version="2026-07-28",
                capabilities={"extensions": {TASKS_EXTENSION_ID: {}}},
            )
        )
        set_task_relay_wired(True)
        enable_governed_task_relay(Mock(), relay_tasks_enabled=False)

        assert forwardable_client_capabilities() is None


class TestTheProtocolModuleStaysALeaf:
    def test_it_does_not_import_the_server_layer(self):
        source = pathlib.Path(protocol_module.__file__).read_text(encoding="utf-8")
        offenders = [
            line
            for line in source.splitlines()
            if line.strip().startswith(("from .server", "from mcp_hangar.server", "import mcp_hangar.server"))
        ]
        assert offenders == [], f"protocol.py reaches into the server layer again: {offenders}"
