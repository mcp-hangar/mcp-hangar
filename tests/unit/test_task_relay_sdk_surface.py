"""SDK-surface guard for the custom ``tasks/*`` relay methods (ADR-014).

The relay seam registers four custom request methods -- ``tasks/get``,
``tasks/result``, ``tasks/cancel``, ``tasks/list`` -- that are DELIBERATELY
absent from the SDK's spec-method registry in mcp==2.0.0b2. That absence is
load-bearing:

``mcp/server/runner.py`` serializes a handler result via
``mcp_types.methods.serialize_server_result(method, version, dumped)`` ONLY when
``method in mcp_types.methods.SPEC_CLIENT_METHODS`` (see ``Runner._serialize``).
For our ``tasks/*`` methods that check is False in b2, so our handlers own their
own result shape and are returned raw. If a b3 promotion ADDS ``tasks/*`` to
``SPEC_CLIENT_METHODS``, the runner would route our results through
``serialize_server_result`` -- which looks the (method, version) pair up in
``SERVER_RESULTS`` and would KeyError / mis-serialize our custom result shape.

This test fails loudly on that b2 -> b3 change so the seam is re-checked before
the SDK bump lands.

FOUND CONSTANT: ``mcp_types.methods.SPEC_CLIENT_METHODS`` (a frozenset), plus
the ``(method, version)`` maps ``SERVER_REQUESTS`` / ``SERVER_RESULTS`` /
``MONOLITH_REQUESTS`` in the same module. The earlier finding's uncertainty is
resolved: the constant exists in b2.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import mcp_types.methods as sdk_methods

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.fastmcp_server.task_relay_handlers import register_task_relay_handlers

# The byte-exact wire method strings the seam registers. Kept as a literal here
# so a rename in the handler registration is caught too.
TASK_RELAY_METHODS = ("tasks/get", "tasks/result", "tasks/cancel", "tasks/list")


class _FakeLow:
    """Captures ``add_request_handler`` registrations from the low-level server."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def add_request_handler(self, method: str, params_type: Any, handler: Any) -> None:
        self.handlers[method] = (params_type, handler)


def _registered_methods() -> set[str]:
    low = _FakeLow()
    mcp = SimpleNamespace(_mcp_server=low)
    from mcp_hangar.domain.services.task_consent import TaskConsentGate

    register_task_relay_handlers(
        mcp, GovernedTaskStore(event_publisher=lambda _e: None), TaskConsentGate(), lambda *a, **k: None
    )
    return set(low.handlers)


def test_seam_registers_exactly_the_four_task_methods() -> None:
    """The seam registers precisely our four tasks/* methods -- no more, no less."""
    assert _registered_methods() == set(TASK_RELAY_METHODS)


def test_task_methods_absent_from_spec_client_methods() -> None:
    """b2 guard: tasks/* MUST NOT be in the SDK's SPEC_CLIENT_METHODS registry.

    Presence here would route our custom result shapes through
    ``serialize_server_result`` (see module docstring) and break the relay.
    A b3 promotion that adds them trips this test.
    """
    spec = sdk_methods.SPEC_CLIENT_METHODS
    offending = [m for m in TASK_RELAY_METHODS if m in spec]
    assert not offending, (
        f"tasks/* method(s) now in SPEC_CLIENT_METHODS: {offending}. An SDK bump promoted them "
        "into the spec surface; the relay seam's raw-serialization path must be re-verified before "
        "adopting the bump (they would otherwise route through serialize_server_result)."
    )


def test_task_methods_absent_from_server_result_registry() -> None:
    """Corollary guard: no (tasks/*, version) result is registered by the SDK.

    ``serialize_server_result`` looks the (method, version) pair up in
    ``SERVER_RESULTS``; our custom methods must have no entry there.
    """
    server_result_methods = {method for (method, _version) in sdk_methods.SERVER_RESULTS}
    monolith_requests = set(sdk_methods.MONOLITH_REQUESTS)
    offending = [m for m in TASK_RELAY_METHODS if m in server_result_methods or m in monolith_requests]
    assert not offending, f"tasks/* method(s) now in the SDK result/request registry: {offending}"
