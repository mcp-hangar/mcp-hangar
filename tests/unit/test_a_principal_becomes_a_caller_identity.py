"""An authenticated `Principal` becomes the `CallerIdentity` the tools read.

`_principal_to_identity_context` is the one translation between what
authentication produced and what a tool sees in `get_identity_context()`. Get a
`principal_type` wrong here and a service account is billed, rate-limited and
audited as a user.

This file used to also drive `create_auth_combined_app` end to end, asserting
that the combiner bound `identity_context_var` itself. That combiner was only
ever reachable through `MCPServerFactory.create_asgi_app`, which the shipped
gateway never calls, and it was deleted in #955. The live path does not bind
identity in an ASGI wrapper at all: since #576 `mcp_tool_wrapper` sets
`identity_context_var` from the authenticated principal before the tool body
runs -- see the note in `server/tools/mcp_server.py`. The mapping below is the
part both paths shared, so it is the part that stays.
"""

from __future__ import annotations


from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server.asgi import _principal_to_identity_context


# ---------------------------------------------------------------------------
# Unit tests for the mapping helper (no ASGI overhead)
# ---------------------------------------------------------------------------


class TestPrincipalToIdentityContext:
    """Tests for the _principal_to_identity_context bridge helper."""

    def test_user_principal_maps_correctly(self):
        principal = Principal(
            id=PrincipalId("user:alice"),
            type=PrincipalType.USER,
            tenant_id="tenant-abc",
        )
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "user"
        assert ctx.caller.user_id == "user:alice"
        assert ctx.caller.tenant_id == "tenant-abc"
        assert ctx.caller.agent_id is None
        assert ctx.caller.session_id is None

    def test_service_account_principal_maps_to_service(self):
        principal = Principal(
            id=PrincipalId("svc-ci-pipeline"),
            type=PrincipalType.SERVICE_ACCOUNT,
            tenant_id="tenant-xyz",
        )
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "service"
        assert ctx.caller.user_id == "svc-ci-pipeline"
        assert ctx.caller.tenant_id == "tenant-xyz"

    def test_system_principal_maps_to_service(self):
        """SYSTEM maps to 'service' — the closest valid Literal for non-human identity."""
        principal = Principal.system()
        ctx = _principal_to_identity_context(principal)
        # system() has id="system", type=SYSTEM, no tenant
        assert ctx.caller.principal_type == "service"
        assert ctx.caller.user_id == "system"
        assert ctx.caller.tenant_id is None

    def test_anonymous_principal_maps_to_anonymous(self):
        principal = Principal.anonymous()
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "anonymous"
        assert ctx.caller.user_id is None
        assert ctx.caller.tenant_id is None

    def test_none_principal_maps_to_anonymous(self):
        ctx = _principal_to_identity_context(None)
        assert ctx.caller.principal_type == "anonymous"
        assert ctx.caller.user_id is None
        assert ctx.caller.tenant_id is None

    def test_user_without_tenant_id(self):
        principal = Principal(
            id=PrincipalId("user:bob"),
            type=PrincipalType.USER,
            tenant_id=None,
        )
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "user"
        assert ctx.caller.tenant_id is None

    def test_caller_identity_post_init_satisfied(self):
        """user_id must be non-None for 'user'/'service' — bridge must satisfy this."""
        from mcp_hangar.domain.value_objects.identity import CallerIdentity

        for p_type in (PrincipalType.USER, PrincipalType.SERVICE_ACCOUNT, PrincipalType.SYSTEM):
            principal = Principal(
                id=PrincipalId("some-id"),
                type=p_type,
                tenant_id="t1",
            )
            ctx = _principal_to_identity_context(principal)
            # Validate that the CallerIdentity is internally consistent
            assert isinstance(ctx.caller, CallerIdentity)
            if ctx.caller.principal_type in ("user", "service"):
                assert ctx.caller.user_id is not None


# ---------------------------------------------------------------------------
# Integration tests: drive create_auth_combined_app end-to-end
# ---------------------------------------------------------------------------


async def _null_receive():
    return {"type": "http.disconnect"}
