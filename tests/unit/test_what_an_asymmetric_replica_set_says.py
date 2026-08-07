"""Three things a replica set would not tell you, found by building lopsided ones.

Replicas are usually described as identical. Real ones drift: one carries the
discovery credentials, one keeps a stale ConfigMap, one is mid-rollout. Each of
these was measured on a deliberately asymmetric two-replica deployment, and each
was invisible from outside at the moment it mattered.

1. Discovery configured on a replica that does not hold the lease runs **zero
   cycles** and says nothing, while its boot log already claimed a source count.
2. The failover time in force is the *holder's* `lease_ttl_s`, not the waiting
   replica's. A replica configured for 10s waited 52.
3. A follower refusing to start a local-mode server answered **500**, which
   says the gateway is broken about a gateway doing exactly its job.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from mcp_hangar.domain.contracts.management_lease import Lease


class _Store:
    """A lease store with one row, controllable from the test."""

    def __init__(self, incumbent: Lease | None = None) -> None:
        self.incumbent = incumbent
        self.current_calls = 0

    def acquire(self, holder: str, ttl_s: float) -> Lease | None:
        return None if self.incumbent is not None else Lease(holder, 1, time.time() + ttl_s)

    def renew(self, lease: Lease, ttl_s: float) -> Lease | None:
        return None

    def release(self, lease: Lease) -> None:
        return None

    def current(self) -> Lease | None:
        self.current_calls += 1
        return self.incumbent


def _keeper(store: Any, ttl_s: float = 10.0):
    from mcp_hangar.application.services.lease_keeper import ManagementLeaseKeeper

    return ManagementLeaseKeeper(store, "me", ttl_s=ttl_s, interval_s=0.01, renew_deadline_s=ttl_s * 0.8)


class TestTheTenureInForceIsNotThisInstancesIdea:
    def test_a_follower_learns_who_holds_it_and_for_how_long(self) -> None:
        incumbent = Lease("the-other-one", 7, time.time() + 60)
        keeper = _keeper(_Store(incumbent))

        keeper._try_acquire()

        assert keeper.lease is None, "this instance does not hold it"
        assert keeper.incumbent is not None
        assert keeper.incumbent.holder == "the-other-one"
        assert keeper.incumbent.generation == 7

    def test_the_holder_reports_its_own_lease_not_a_stale_observation(self) -> None:
        keeper = _keeper(_Store(incumbent=None))

        keeper._try_acquire()

        assert keeper.lease is not None
        assert keeper.incumbent is keeper.lease

    def test_a_longer_tenure_than_configured_is_reported(self) -> None:
        # The measured case: this replica is configured for 10s and the holder
        # wrote 60. Nothing else in the system states the number being waited on.
        keeper = _keeper(_Store(Lease("the-slow-one", 3, time.time() + 60)), ttl_s=10.0)

        with capture_logs() as logs:
            keeper._try_acquire()

        reported = [entry for entry in logs if entry["event"] == "management_lease_tenure_longer_than_configured"]
        assert len(reported) == 1
        assert reported[0]["holder"] == "the-slow-one"
        assert reported[0]["my_lease_ttl_s"] == 10.0

    def test_a_normal_tenure_is_not_reported(self) -> None:
        keeper = _keeper(_Store(Lease("a-peer", 3, time.time() + 9)), ttl_s=10.0)

        with capture_logs() as logs:
            keeper._try_acquire()

        assert not [e for e in logs if "tenure_longer_than_configured" in e["event"]]

    def test_an_unreadable_row_is_not_a_reason_to_stop(self) -> None:
        # This path only reports. A store that raises here must not break the
        # acquisition loop that was already returning "not the holder".
        class _Broken(_Store):
            def current(self) -> Lease | None:
                raise RuntimeError("gone")

        keeper = _keeper(_Broken(Lease("someone", 1, time.time() + 30)))

        keeper._try_acquire()  # must not raise

        assert keeper.incumbent is None


class TestApiSystemNamesTheInstanceToAsk:
    def test_the_lease_block_carries_holder_and_remaining(self) -> None:
        from mcp_hangar.server.api.system import _lease_info

        keeper = MagicMock()
        keeper.incumbent = Lease("gw-abc", 12, time.time() + 42)
        keeper.ttl_s = 10.0

        info = _lease_info(keeper)

        assert info is not None
        assert info["holder"] == "gw-abc"
        assert info["generation"] == 12
        assert 40 <= info["expires_in_s"] <= 43
        # Reported next to the observed one precisely so drift is readable.
        assert info["my_lease_ttl_s"] == 10.0

    def test_no_lease_anywhere_is_absence_not_a_guess(self) -> None:
        from mcp_hangar.server.api.system import _lease_info

        keeper = MagicMock()
        keeper.incumbent = None

        assert _lease_info(keeper) is None


class TestDiscoverySaysWhenItIsNotTheOneDiscovering:
    def _orchestrator(self, may_manage):
        from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryOrchestrator

        return DiscoveryOrchestrator(static_mcp_servers=set(), may_manage=may_manage)

    def test_the_first_skipped_cycle_is_reported(self) -> None:
        orchestrator = self._orchestrator(lambda: False)

        with capture_logs() as logs:
            assert orchestrator._holds_the_lease() is False

        assert [e for e in logs if e["event"] == "discovery_idle_not_the_lease_holder"]

    def test_it_does_not_say_it_every_cycle(self) -> None:
        orchestrator = self._orchestrator(lambda: False)

        with capture_logs() as logs:
            for _ in range(9):
                orchestrator._holds_the_lease()

        assert len([e for e in logs if e["event"] == "discovery_idle_not_the_lease_holder"]) == 1

    def test_taking_over_is_worth_one_line(self) -> None:
        holds = {"value": False}
        orchestrator = self._orchestrator(lambda: holds["value"])
        orchestrator._holds_the_lease()

        holds["value"] = True
        with capture_logs() as logs:
            assert orchestrator._holds_the_lease() is True

        assert [e for e in logs if e["event"] == "discovery_resumed_on_this_instance"]

    def test_a_holder_that_never_idled_stays_quiet(self) -> None:
        orchestrator = self._orchestrator(lambda: True)

        with capture_logs() as logs:
            for _ in range(3):
                assert orchestrator._holds_the_lease() is True

        assert not [e for e in logs if e["event"] == "discovery_resumed_on_this_instance"]


class TestARefusalIsNotAFault:
    def test_the_launcher_refusal_is_a_domain_error(self) -> None:
        from mcp_hangar.domain.exceptions import McpServerNotHereError
        from mcp_hangar.infrastructure.launchers.factory import LocalModeNotOwnedError

        assert issubclass(LocalModeNotOwnedError, McpServerNotHereError)

    def test_it_maps_to_409_not_500(self) -> None:
        # 500 says "this gateway is broken" about a gateway behaving exactly as
        # designed. 409 says "not here", which the caller can act on.
        from mcp_hangar.infrastructure.launchers.factory import LocalModeNotOwnedError
        from mcp_hangar.server.api.middleware import _get_status_code

        assert _get_status_code(LocalModeNotOwnedError("subprocess")) == 409

    def test_a_real_start_failure_is_still_a_500(self) -> None:
        from mcp_hangar.domain.exceptions import McpServerStartError
        from mcp_hangar.server.api.middleware import _get_status_code

        assert _get_status_code(McpServerStartError(mcp_server_id="x", reason="the binary is missing")) == 500

    def test_the_message_names_the_instance_to_ask(self) -> None:
        from mcp_hangar.infrastructure.launchers.factory import LocalModeNotOwnedError

        message = str(LocalModeNotOwnedError("docker"))

        assert "manages_fleet" in message, "the caller needs to know which replica to ask"
        assert "remote" in message


@pytest.mark.parametrize("mode", ["subprocess", "docker"])
def test_the_model_lets_the_refusal_through_unwrapped(mode: str) -> None:
    """Wrapping it in McpServerStartError is what produced the 500."""
    import inspect

    from mcp_hangar.domain.model import mcp_server

    source = inspect.getsource(mcp_server)

    assert "except McpServerNotHereError" in source
    assert source.index("except McpServerNotHereError") < source.index("except McpServerStartError as e:")
