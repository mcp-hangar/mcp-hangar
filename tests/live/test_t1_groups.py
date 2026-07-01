"""Tier 1 live verification: groups, canary/failover, discovery (multi-backend).

Placeholder skeleton -- these need a multi-backend compose harness (>=2 member
servers). Implement on top of a compose fixture (reuse examples/discovery or a
dedicated group compose). Tracked in docs/internal/LIVE_VERIFICATION.md.
"""

import pytest

pytestmark = [pytest.mark.live, pytest.mark.t1]


@pytest.mark.skip(reason="T1 multi-backend compose harness -- follow-up (see LIVE_VERIFICATION.md)")
def test_group_invocation_routes_to_a_member():
    """Claim: a hangar_call to a group dispatches to a selected member (#282)."""


@pytest.mark.skip(reason="T1 multi-backend compose harness -- follow-up")
def test_canary_pins_a_tenant_to_a_version():
    """Claim: a pinned tenant deterministically hits its pinned member; a split
    routes ~split_pct of tenants to the canary member (#283)."""
