"""Readiness must not require a warm backend (#599).

Hangar starts backends lazily and stops them on ``idle_ttl_s``, so "every
backend cold" is the normal steady state of an idle gateway. Requiring a warm
backend deadlocked Kubernetes: last backend goes idle -> 503 -> pod leaves the
Service endpoints -> no call can arrive -> nothing warms a backend again.

These tests pin the readiness *decision*; the endpoint is a two-line wrapper
over it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_hangar.observability.health import (
    EventStoreDurabilityStatus,
    set_event_store_durability_status,
)
from mcp_hangar.server.lifecycle import build_readiness_report


class _Repository:
    """Minimal stand-in exposing the two members the report reads."""

    def __init__(self, *states: str) -> None:
        self._servers = {f"s{i}": SimpleNamespace(state=SimpleNamespace(value=state)) for i, state in enumerate(states)}

    def get_all(self) -> dict:
        return self._servers

    def count(self) -> int:
        return len(self._servers)


@pytest.fixture(autouse=True)
def _clean_durability():
    """The durability status is process-global; keep tests from leaking into each other."""
    set_event_store_durability_status(None)
    yield
    set_event_store_durability_status(None)


class TestBackendStateDoesNotGateReadiness:
    def test_all_backends_cold_is_ready(self):
        """The regression: an idle gateway with every backend cold is READY."""
        body, status = build_readiness_report(_Repository("cold", "cold"))

        assert status == 200
        assert body["status"] == "healthy"
        assert body["ready_mcp_servers"] == 0
        assert body["total_mcp_servers"] == 2

    def test_no_backends_configured_is_ready(self):
        body, status = build_readiness_report(_Repository())

        assert status == 200
        assert body["total_mcp_servers"] == 0

    def test_a_warm_backend_is_still_ready_and_counted(self):
        body, status = build_readiness_report(_Repository("ready", "cold"))

        assert status == 200
        assert body["ready_mcp_servers"] == 1
        assert body["total_mcp_servers"] == 2

    def test_even_all_dead_backends_keep_the_gateway_ready(self):
        """Pulling the gateway out of the Service cannot revive a backend.

        It only hides the REST API and metrics from the operator trying to
        diagnose it, and every replica shares the same config so there is
        nowhere healthier to route. Backend health is an alerting signal, which
        is why the health framework marks it ``critical=False``.
        """
        body, status = build_readiness_report(_Repository("dead", "dead"))

        assert status == 200
        assert body["ready_mcp_servers"] == 0


class TestEventStoreDurabilityStillGatesReadiness:
    def test_degraded_durable_store_is_not_ready(self):
        """Preserved: silently losing the audit trail must fail readiness."""
        set_event_store_durability_status(
            EventStoreDurabilityStatus(
                configured_driver="sqlite",
                durable=False,
                degraded=True,
                detail="path not writable; degraded to in-memory",
            )
        )

        body, status = build_readiness_report(_Repository("ready"))

        assert status == 503
        assert body["status"] == "unhealthy"
        assert body["event_store"]["configured_driver"] == "sqlite"
        assert body["event_store"]["durable"] is False

    def test_explicit_memory_driver_is_ready(self):
        """Asking for memory and getting memory is not a degradation."""
        set_event_store_durability_status(
            EventStoreDurabilityStatus(
                configured_driver="memory",
                durable=False,
                degraded=False,
                detail="explicit memory",
            )
        )

        _body, status = build_readiness_report(_Repository("cold"))

        assert status == 200
