"""Per-tenant canary / version routing (#275).

Covers three layers of the feature:

1. ``CanaryPolicy.resolve`` -- pure resolution logic: an explicit pin wins, a
   sticky split is deterministic and lands roughly ``split_pct``% of tenants on
   the canary member, and an empty / zero-split policy resolves to None.
2. ``McpServerGroup.select_member_for`` -- applies the policy against actual
   in-rotation members, falling back to the load balancer when the resolved
   target is missing or out of rotation.
3. ``_load_group_config`` -- parses a ``canary`` block, validates targets
   against real group members, and warn-skips invalid entries.
"""

from unittest.mock import MagicMock

import pytest

from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, McpServerGroup
from mcp_hangar.domain.value_objects import ProviderState


def _mock_member(mcp_server_id: str, state: ProviderState = ProviderState.READY):
    """Build a mock McpServer member that needs no process to start."""
    mock = MagicMock()
    mock.id = mcp_server_id
    mock.mcp_server_id = mcp_server_id
    mock.state = state
    mock.state_snapshot = state
    mock.ensure_ready = MagicMock()
    mock.shutdown = MagicMock()
    mock.tools = []
    mock.get_tool_names = MagicMock(return_value=[])
    return mock


def _group_with_members(*member_ids: str) -> McpServerGroup:
    """Create a group with the given members all placed in rotation."""
    group = McpServerGroup(group_id="canary-group", auto_start=False)
    for mid in member_ids:
        group.add_member(_mock_member(mid))
    # rebalance() flips READY members into rotation (state_snapshot == READY).
    group.rebalance()
    return group


# --------------------------------------------------------------------------- #
# 1. CanaryPolicy.resolve (pure)
# --------------------------------------------------------------------------- #


class TestCanaryPolicyResolve:
    def test_pin_wins_over_split(self):
        """An explicit per-tenant pin takes precedence over a configured split."""
        policy = CanaryPolicy(
            canary_member="v2",
            split_pct=100,  # split would otherwise route everyone to v2
            pinned_tenants={"tenant:acme": "v1"},
        )
        assert policy.resolve("tenant:acme") == "v1"

    def test_split_is_deterministic_per_tenant(self):
        """The same tenant id always resolves to the same target."""
        policy = CanaryPolicy(canary_member="v2", split_pct=50)
        first = policy.resolve("tenant:repeat")
        for _ in range(20):
            assert policy.resolve("tenant:repeat") == first

    def test_split_routes_roughly_split_pct_to_canary(self):
        """Across many tenants, ~split_pct% land on the canary member."""
        policy = CanaryPolicy(canary_member="v2", split_pct=10)
        ids = [f"tenant:{i}" for i in range(2000)]
        on_canary = sum(1 for t in ids if policy.resolve(t) == "v2")
        fraction = on_canary / len(ids)
        # split_pct=10 -> expect ~10%; assert a generous band to avoid flakiness.
        assert 0.05 <= fraction <= 0.18

    def test_zero_split_with_no_pin_resolves_none(self):
        """split_pct=0 and an un-pinned tenant -> use the load balancer (None)."""
        policy = CanaryPolicy(canary_member="v2", split_pct=0)
        assert policy.resolve("tenant:anyone") is None

    def test_empty_canary_member_with_split_resolves_none(self):
        """No canary member set -> split is inert, returns None."""
        policy = CanaryPolicy(canary_member="", split_pct=100)
        assert policy.resolve("tenant:anyone") is None

    def test_default_policy_resolves_none(self):
        """A default / empty policy never routes anywhere."""
        policy = CanaryPolicy()
        assert policy.resolve("tenant:anyone") is None


# --------------------------------------------------------------------------- #
# 2. McpServerGroup.select_member_for
# --------------------------------------------------------------------------- #


class TestSelectMemberFor:
    def test_pinned_tenant_routes_to_pinned_member(self):
        """A pinned tenant is routed to its pinned member's McpServer."""
        group = _group_with_members("v1", "v2")
        group.set_canary_policy(CanaryPolicy(pinned_tenants={"tenant:acme": "v2"}))

        selected = group.select_member_for("tenant:acme")

        assert selected is group.get_member("v2").mcp_server
        # Repeated calls stay sticky regardless of LB round-robin state.
        for _ in range(5):
            assert group.select_member_for("tenant:acme") is group.get_member("v2").mcp_server

    def test_unpinned_tenant_falls_back_to_load_balancer(self):
        """An un-pinned tenant (with no split) uses the LB, returning a member."""
        group = _group_with_members("v1", "v2")
        group.set_canary_policy(CanaryPolicy(pinned_tenants={"tenant:acme": "v2"}))

        in_rotation = {group.get_member("v1").mcp_server, group.get_member("v2").mcp_server}
        selected = group.select_member_for("tenant:other")

        assert selected in in_rotation

    def test_none_tenant_uses_load_balancer(self):
        """select_member_for(None) ignores the policy and uses the LB."""
        group = _group_with_members("v1", "v2")
        group.set_canary_policy(CanaryPolicy(pinned_tenants={"tenant:acme": "v2"}))

        in_rotation = {group.get_member("v1").mcp_server, group.get_member("v2").mcp_server}
        assert group.select_member_for(None) in in_rotation

    def test_pinned_target_out_of_rotation_falls_back_to_lb(self):
        """If the pinned target is out of rotation, never select it -- use the LB."""
        group = _group_with_members("v1", "v2")
        group.set_canary_policy(CanaryPolicy(pinned_tenants={"tenant:acme": "v2"}))

        # Force v2 out of rotation; only v1 remains available.
        group.get_member("v2").in_rotation = False

        selected = group.select_member_for("tenant:acme")

        assert selected is group.get_member("v1").mcp_server
        assert selected is not group.get_member("v2").mcp_server

    def test_select_member_delegates_to_select_member_for_none(self):
        """The no-arg select_member() still works (delegates to None tenant)."""
        group = _group_with_members("v1", "v2")
        group.set_canary_policy(CanaryPolicy(pinned_tenants={"tenant:acme": "v2"}))

        in_rotation = {group.get_member("v1").mcp_server, group.get_member("v2").mcp_server}
        assert group.select_member() in in_rotation


# --------------------------------------------------------------------------- #
# 3. Config parsing (_load_group_config)
# --------------------------------------------------------------------------- #


class TestCanaryConfigParsing:
    @pytest.fixture(autouse=True)
    def reset_globals(self):
        """Isolate the shared repository and GROUPS registry around each test."""
        from mcp_hangar.server.state import get_runtime, GROUPS

        repository = get_runtime().repository
        original_providers = repository.get_all()
        original_groups = dict(GROUPS)

        yield

        repository.clear()
        for mcp_server_id, provider in original_providers.items():
            repository.add(mcp_server_id, provider)
        GROUPS.clear()
        GROUPS.update(original_groups)

    @staticmethod
    def _group_spec(canary: dict) -> dict:
        return {
            "mode": "group",
            "auto_start": False,  # do not start real subprocesses
            "members": [
                {"id": "m1", "mode": "subprocess", "command": ["cmd1"]},
                {"id": "m2", "mode": "subprocess", "command": ["cmd2"]},
            ],
            "canary": canary,
        }

    def test_valid_canary_block_is_parsed_and_pins_route(self):
        """A valid canary block produces a policy that routes the pinned tenant."""
        from mcp_hangar.server.config import load_config
        from mcp_hangar.server.state import GROUPS

        load_config({"cgroup": self._group_spec({"member": "m2", "split_pct": 10, "pinned_tenants": {"t1": "m1"}})})

        group = GROUPS["cgroup"]
        assert group is not None
        # Inspect the parsed policy directly.
        assert group._canary is not None
        assert group._canary.canary_member == "m2"
        assert group._canary.split_pct == 10
        assert group._canary.pinned_tenants == {"t1": "m1"}
        # And it resolves the pin to the m1 member.
        assert group._canary.resolve("t1") == "m1"

    def test_invalid_canary_member_is_warn_skipped(self):
        """A canary.member that is not a group member is dropped (no raise)."""
        from mcp_hangar.server.config import load_config
        from mcp_hangar.server.state import GROUPS

        load_config({"cgroup": self._group_spec({"member": "not-a-member", "split_pct": 10})})

        group = GROUPS["cgroup"]
        # Invalid member dropped -> no canary member; with no pins, no policy set.
        assert group._canary is None or group._canary.canary_member == ""

    def test_out_of_range_split_pct_is_reset(self):
        """An out-of-range split_pct is warn-skipped and reset to 0 (no raise)."""
        from mcp_hangar.server.config import load_config
        from mcp_hangar.server.state import GROUPS

        load_config({"cgroup": self._group_spec({"member": "m2", "split_pct": 250, "pinned_tenants": {"t1": "m1"}})})

        group = GROUPS["cgroup"]
        # Pin keeps the policy alive, but the bad split was reset to 0.
        assert group._canary is not None
        assert group._canary.split_pct == 0
        assert group._canary.pinned_tenants == {"t1": "m1"}

    def test_pin_to_non_member_is_dropped(self):
        """A pin whose target is not a group member is dropped."""
        from mcp_hangar.server.config import load_config
        from mcp_hangar.server.state import GROUPS

        load_config(
            {
                "cgroup": self._group_spec(
                    {
                        "member": "m2",
                        "split_pct": 10,
                        "pinned_tenants": {"good": "m1", "bad": "ghost"},
                    }
                )
            }
        )

        group = GROUPS["cgroup"]
        assert group._canary is not None
        assert group._canary.pinned_tenants == {"good": "m1"}
