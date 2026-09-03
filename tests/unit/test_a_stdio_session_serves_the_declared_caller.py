"""A stdio session serves the caller its configuration declares (ADR-026, #1190).

Identity reaches Hangar through ASGI middleware, and `run_stdio` never enters
one: no scope, no headers, no request. So every stdio caller was anonymous, and
`front_door` -- fail-closed on identity since #902 -- projected zero tools to the
only transport a laptop uses. ADR-026 makes the spawning process the trust
boundary and lets `auth.stdio.principal` name the caller it implies.

What is pinned here is the whole shape of that decision, because each half of it
fails differently:

1. the block is parsed, and a half-written one is dropped rather than guessed at;
2. it applies on stdio and **only** on stdio -- an HTTP run ignores it, which is
   what keeps the credential channel the only way in over HTTP;
3. the declared identity reaches readers through `get_identity_context`, so the
   flat projection sees a tenant instead of `no_identity`;
4. the management surface follows the declared roles, so `viewer` is read-only
   rather than "everything, because auth is off";
5. per-tenant pins for the declared tenant stop being refused at boot, and pins
   for any other tenant are refused exactly as before.
"""

import pytest

from mcp_hangar.auth.config import parse_auth_config
from mcp_hangar.auth.stdio_principal import clear_stdio_principal, get_stdio_principal
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.bootstrap import _declare_stdio_principal
from mcp_hangar.server.bootstrap.pinning import (
    PinnedToolsNeedAnIdentityError,
    refuse_pins_that_no_caller_can_match,
)
from mcp_hangar.server.tools.tool_permissions import management_tools_for

DECLARED = {
    "auth": {
        "stdio": {
            "principal": {"id": "local-user", "tenant_id": "local", "roles": ["viewer"]},
        }
    }
}


@pytest.fixture(autouse=True)
def _no_leaked_principal():
    """The declaration is process-wide by design; a test must not leave one behind."""
    clear_stdio_principal()
    yield
    clear_stdio_principal()


class TestTheBlockIsParsed:
    def test_a_complete_block_becomes_a_principal_config(self):
        stdio = parse_auth_config(DECLARED["auth"]).stdio

        assert stdio is not None
        assert (stdio.id, stdio.tenant_id, stdio.roles) == ("local-user", "local", ["viewer"])

    def test_roles_default_to_read_only_viewer(self):
        stdio = parse_auth_config({"stdio": {"principal": {"id": "u", "tenant_id": "t"}}}).stdio

        assert stdio is not None
        assert stdio.roles == ["viewer"]

    @pytest.mark.parametrize(
        "principal",
        [
            {"tenant_id": "local"},  # no id
            {"id": "local-user"},  # no tenant
            {"id": "", "tenant_id": "local"},  # blank id
        ],
        ids=["no_id", "no_tenant", "blank_id"],
    )
    def test_a_half_written_principal_is_dropped_not_guessed(self, principal):
        # An undeclared caller, not a partially declared one: admitting it with a
        # default would hand a config typo an identity nobody wrote down.
        assert parse_auth_config({"stdio": {"principal": principal}}).stdio is None

    def test_no_block_leaves_no_principal(self):
        assert parse_auth_config({"enabled": False}).stdio is None


class TestOnlyStdioReadsIt:
    def test_stdio_declares_the_principal(self):
        _declare_stdio_principal(DECLARED, stdio=True)

        principal = get_stdio_principal()
        assert principal is not None
        assert principal.id.value == "local-user"
        assert principal.tenant_id == "local"

    def test_http_ignores_the_block(self):
        # HTTP has a credential channel; the declaration must not become a second
        # way in on a transport that can check one.
        _declare_stdio_principal(DECLARED, stdio=False)

        assert get_stdio_principal() is None


class TestTheDeclaredIdentityReachesReaders:
    def test_get_identity_context_falls_back_to_the_declaration(self):
        _declare_stdio_principal(DECLARED, stdio=True)

        identity = get_identity_context()
        assert identity is not None
        assert identity.caller.tenant_id == "local"
        assert identity.caller.user_id == "local-user"
        assert identity.caller.principal_type == "user"

    def test_a_bound_context_still_wins(self):
        # The HTTP path binds per request; the process-wide declaration must never
        # overwrite a caller who arrived with credentials.
        _declare_stdio_principal(DECLARED, stdio=True)
        bound = IdentityContext(
            caller=CallerIdentity(
                user_id="http-user",
                agent_id=None,
                session_id=None,
                principal_type="user",
                tenant_id="other",
            )
        )
        token = identity_context_var.set(bound)
        try:
            assert get_identity_context() is bound
        finally:
            identity_context_var.reset(token)

    def test_without_a_declaration_there_is_no_identity(self):
        assert get_identity_context() is None


class TestTheManagementSurfaceFollowsTheRoles:
    def test_viewer_sees_reads_and_nothing_that_changes_state(self):
        _declare_stdio_principal(DECLARED, stdio=True)

        surface = management_tools_for(None)

        assert "hangar_status" in surface
        assert "hangar_health" in surface
        for mutating in ("hangar_stop", "hangar_start", "hangar_load", "hangar_unload", "hangar_reload_config"):
            assert mutating not in surface

    def test_an_empty_role_list_projects_no_management_surface(self):
        _declare_stdio_principal(
            {"auth": {"stdio": {"principal": {"id": "u", "tenant_id": "t", "roles": []}}}},
            stdio=True,
        )

        assert management_tools_for(None) == frozenset()

    def test_an_unknown_role_grants_nothing(self):
        _declare_stdio_principal(
            {"auth": {"stdio": {"principal": {"id": "u", "tenant_id": "t", "roles": ["wizard"]}}}},
            stdio=True,
        )

        assert management_tools_for(None) == frozenset()


class TestPinsForTheDeclaredTenant:
    CONFIG = {
        **DECLARED,
        "mcp_servers": {
            "probe": {"tool_projection": {"tenant_overrides": {"local": {"pins": {"echo": "abc"}}}}},
        },
    }

    def test_pins_for_the_declared_tenant_boot(self):
        _declare_stdio_principal(DECLARED, stdio=True)

        refuse_pins_that_no_caller_can_match(self.CONFIG)  # does not raise

    def test_pins_for_another_tenant_are_still_refused(self):
        _declare_stdio_principal(DECLARED, stdio=True)
        config = {
            **DECLARED,
            "mcp_servers": {
                "probe": {"tool_projection": {"tenant_overrides": {"finance": {"pins": {"echo": "abc"}}}}},
            },
        }

        with pytest.raises(PinnedToolsNeedAnIdentityError):
            refuse_pins_that_no_caller_can_match(config)

    def test_without_a_declaration_the_refusal_is_unchanged(self):
        with pytest.raises(PinnedToolsNeedAnIdentityError):
            refuse_pins_that_no_caller_can_match(self.CONFIG)
