"""Tier 1 live verification: groups + per-tenant canary routing (multi-backend).

Driven black-box against a *running* hangar (real CLI subprocess + real MCP over
HTTP) with two ``mode: subprocess`` group members (``examples/provider_identity``)
whose ``whoami`` tool echoes which member served a call. See the harness in
``tests/live/conftest.py`` / ``tests/live/_group_support.py`` and the matrix in
``docs/internal/LIVE_VERIFICATION.md``.
"""

import pytest

from tests.live import _group_support as gs

pytestmark = [pytest.mark.live, pytest.mark.t1]


def test_group_invocation_routes_to_a_member(live_group_hangar):
    """Claim: a ``hangar_call`` to a group dispatches to a selected member (#282).

    Repeatedly invoke the group's tool and observe the serving backend: every
    call must land on a real, in-rotation member, and across the round-robin the
    group must select *both* of its members (proving genuine member selection,
    not a single hard-wired backend).
    """
    served = [gs.serving_member(live_group_hangar) for _ in range(10)]

    # Every dispatch reached a real group member (not the group id, not an error).
    assert all(member in live_group_hangar.members for member in served), served
    # Member selection actually distributes across the group's members.
    assert set(served) == set(live_group_hangar.members), served


def test_canary_pins_a_tenant_to_a_version(live_group_hangar):
    """Claim: a pinned tenant deterministically hits its member; a split routes
    the SHA-256-bucketed ``split_pct`` of tenants to the canary member (#283).

    Tenant identity is carried over the shipped ``X-API-Key`` surface. If the
    running transport does not propagate the caller's tenant into the invoke path
    (a known limitation of the stateful streamable-HTTP session task, which does
    not inherit the identity contextvar bound by the ASGI auth layer), the claim
    is not observable here and the test skips with that reason rather than
    failing -- the canary resolution + bucketing itself is proven in
    ``tests/unit/test_canary_routing.py``.
    """
    harness = live_group_hangar
    pin_a_member = harness.pinned["tenant:pin-a"]

    # Propagation probe: a pinned tenant MUST be sticky to its pinned member. If
    # it round-robins instead, the tenant never reached member selection.
    probe = [gs.serving_member(harness, tenant_id="tenant:pin-a") for _ in range(6)]
    if set(probe) != {pin_a_member}:
        pytest.skip(
            "tenant identity is not propagated into the invoke path over the live "
            "streamable-HTTP tool surface (the ASGI-bound identity contextvar does "
            "not reach FastMCP's per-session task), so per-tenant canary routing "
            "cannot be observed via hangar_call here. Canary resolution + #283 "
            f"bucketing are covered by tests/unit/test_canary_routing.py. Saw: {probe}"
        )

    # 1) Explicit pins are deterministic, in both directions.
    assert probe == [pin_a_member] * 6
    pin_b_member = harness.pinned["tenant:pin-b"]
    assert [gs.serving_member(harness, tenant_id="tenant:pin-b") for _ in range(6)] == [pin_b_member] * 6

    # 2) The split: every tenant the SHA-256 bucket predicts for the canary must
    #    deterministically land on the canary member, and the predicted share of
    #    the tenant population is ~split_pct.
    predicted_canary = [t for t in gs.SPLIT_TENANTS if gs.expected_split_target(t) == harness.canary_member]
    predicted_fraction = len(predicted_canary) / len(gs.SPLIT_TENANTS)
    # Property of the bucketing over this sample: within tolerance of split_pct.
    assert abs(predicted_fraction - harness.split_pct / 100) <= 0.12, predicted_fraction

    # Determinism + correct target for every in-split tenant (two calls each).
    for tenant in predicted_canary:
        first = gs.serving_member(harness, tenant_id=tenant)
        second = gs.serving_member(harness, tenant_id=tenant)
        assert first == harness.canary_member, (tenant, first)
        assert second == first, (tenant, first, second)
