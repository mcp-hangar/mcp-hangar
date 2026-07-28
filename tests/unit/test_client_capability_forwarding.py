"""Hangar forwards the caller's Tasks declaration upstream, per request.

Two halves, and the first is a bug the second could not have worked around.

**Reading.** `read_protocol_negotiation` looked for
`io.modelcontextprotocol/capabilities`. The spec key is
`io.modelcontextprotocol/clientCapabilities` -- the SDK's inbound ladder requires
it on every modern request, and the short spelling appears nowhere in
`mcp_types`. So capabilities came back empty for every well-formed request. It
went unnoticed because nothing consumed them.

**Forwarding.** SEP-2663 leaves task augmentation to the *upstream* and gates it
on the **caller** having declared the extension. On the wire to an upstream,
Hangar is that caller. Declaring nothing means a spec-following upstream never
mints a task, and the whole governed relay sits idle having never been offered
one.

The forwarding is conditional on purpose, and each condition excludes a specific
way of lying to an upstream.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.negotiation import (
    ProtocolNegotiation,
    read_protocol_negotiation,
    set_current_protocol_negotiation,
)
from mcp_hangar.protocol import (
    TASKS_EXTENSION_ID,
    forwardable_client_capabilities,
    inject_protocol_meta,
)

_SPEC_KEY = "io.modelcontextprotocol/clientCapabilities"
_LEGACY_KEY = "io.modelcontextprotocol/capabilities"
_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"

_DECLARED: dict[str, Any] = {"extensions": {TASKS_EXTENSION_ID: {}}}


@pytest.fixture
def declaring_caller():
    """Bind a request whose caller declared the Tasks extension."""
    set_current_protocol_negotiation(ProtocolNegotiation(protocol_version="2026-07-28", capabilities=_DECLARED))
    yield
    set_current_protocol_negotiation(ProtocolNegotiation())


@pytest.fixture
def relay_wired(monkeypatch: pytest.MonkeyPatch):
    """Pretend the governed relay is wired, without booting a server."""
    import mcp_hangar.server.context as context_module

    monkeypatch.setattr(
        context_module, "get_context", lambda: SimpleNamespace(governed_task_store=object()), raising=False
    )
    yield


class TestReadingTheCallersDeclaration:
    def test_the_spec_key_is_read(self):
        """`clientCapabilities`, not `capabilities`.

        The SDK's inbound ladder requires this exact key on every modern
        request, so reading the other spelling meant reading nothing, always.
        """
        negotiation = read_protocol_negotiation({_VERSION_KEY: "2026-07-28", _SPEC_KEY: _DECLARED})

        assert dict(negotiation.capabilities) == _DECLARED

    def test_the_legacy_spelling_still_parses(self):
        """Accepted so a caller that copied the old key keeps working."""
        negotiation = read_protocol_negotiation({_LEGACY_KEY: _DECLARED})

        assert dict(negotiation.capabilities) == _DECLARED

    def test_the_spec_key_wins_over_the_legacy_one(self):
        negotiation = read_protocol_negotiation({_SPEC_KEY: _DECLARED, _LEGACY_KEY: {"extensions": {"other": {}}}})

        assert dict(negotiation.capabilities) == _DECLARED

    def test_garbage_is_still_fail_safe(self):
        """The reader must never raise on a hostile envelope."""
        assert dict(read_protocol_negotiation({_SPEC_KEY: "not-a-mapping"}).capabilities) == {}
        assert dict(read_protocol_negotiation(None).capabilities) == {}


class TestForwardingTheDeclaration:
    def test_a_declaring_caller_with_the_relay_wired_is_forwarded(self, declaring_caller, relay_wired):
        assert forwardable_client_capabilities() == {"extensions": {TASKS_EXTENSION_ID: {}}}

    def test_it_lands_on_the_outbound_meta(self, declaring_caller, relay_wired):
        """This is what actually reaches the upstream's inbound ladder."""
        params = inject_protocol_meta({"name": "some_tool", "arguments": {}})

        assert params["_meta"][_SPEC_KEY] == {"extensions": {TASKS_EXTENSION_ID: {}}}
        # Non-mutating, per the function's contract.
        assert "arguments" in params

    def test_a_caller_that_declared_nothing_is_not_spoken_for(self, relay_wired):
        """A connection-level claim would mint tasks for clients that never asked.

        And Hangar would then answer that same client `-32021` on `tasks/get`,
        leaving it holding a handle it cannot use. The two ends have to agree.
        """
        set_current_protocol_negotiation(ProtocolNegotiation(protocol_version="2026-07-28"))

        assert forwardable_client_capabilities() is None
        assert _SPEC_KEY not in inject_protocol_meta({})["_meta"]

    def test_nothing_is_claimed_while_the_relay_is_off(self, declaring_caller, monkeypatch):
        """Claiming it with no governed store promises governance that is not running."""
        import mcp_hangar.server.context as context_module

        monkeypatch.setattr(
            context_module, "get_context", lambda: SimpleNamespace(governed_task_store=None), raising=False
        )

        assert forwardable_client_capabilities() is None

    def test_only_the_tasks_extension_is_relayed(self, declaring_caller, relay_wired, monkeypatch):
        """Not a passthrough: Hangar claims only what it can itself service.

        Forwarding an arbitrary declaration would have Hangar vouch for
        extensions it does not implement on the caller's behalf.
        """
        set_current_protocol_negotiation(
            ProtocolNegotiation(
                protocol_version="2026-07-28",
                capabilities={"extensions": {TASKS_EXTENSION_ID: {}, "com.example/other": {"a": 1}}},
            )
        )

        assert forwardable_client_capabilities() == {"extensions": {TASKS_EXTENSION_ID: {}}}

    def test_a_caller_set_key_is_not_clobbered(self, declaring_caller, relay_wired):
        """`inject_protocol_meta` is set-if-absent for every key it manages."""
        params = inject_protocol_meta({"_meta": {_SPEC_KEY: {"extensions": {}}}})

        assert params["_meta"][_SPEC_KEY] == {"extensions": {}}

    def test_a_broken_context_degrades_to_declaring_nothing(self, declaring_caller, monkeypatch):
        """Fault barrier: a capability read must never fail an invoke.

        Degrading to "declare nothing" is the safe direction -- it loses task
        augmentation, it does not break the call.
        """
        import mcp_hangar.server.context as context_module

        def _boom():
            raise RuntimeError("no application context")

        monkeypatch.setattr(context_module, "get_context", _boom, raising=False)

        assert forwardable_client_capabilities() is None
        assert inject_protocol_meta({})["_meta"][_VERSION_KEY]  # the rest still works
