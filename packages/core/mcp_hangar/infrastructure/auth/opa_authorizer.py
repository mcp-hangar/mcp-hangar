"""OPA (Open Policy Agent) authorization implementation.

Provides integration with OPA for complex policy-based authorization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from ...domain.contracts.authorization import AuthorizationRequest, AuthorizationResult, IPolicyEngine

if TYPE_CHECKING:
    from .rbac_authorizer import RBACAuthorizer

logger = structlog.get_logger(__name__)


class OPAAuthorizer(IPolicyEngine):
    """Open Policy Agent integration for complex authorization policies.

    Evaluates authorization requests against OPA policies via HTTP API.
    Fails closed (denies access) on any errors to ensure security.

    Example OPA policy (policies/mcp_authz.rego):

        package mcp.authz

        default allow := false

        # Admins can do anything
        allow if {
            "admin" in input.principal.groups
        }

        # Developers can invoke tools
        allow if {
            "developer" in input.principal.groups
            input.resource.type == "tool"
            input.action == "invoke"
        }
    """

    def __init__(
        self,
        opa_url: str,
        policy_path: str = "v1/data/mcp/authz/allow",
        timeout: float = 5.0,
    ):
        """Initialize the OPA authorizer.

        Args:
            opa_url: Base URL of the OPA server (e.g., http://localhost:8181).
            policy_path: Path to the policy decision endpoint.
            timeout: HTTP request timeout in seconds.
        """
        self._opa_url = opa_url.rstrip("/")
        self._policy_path = policy_path.lstrip("/")
        self._timeout = timeout
        self._client = None  # Lazy initialization

    def evaluate(self, input_data: dict[str, Any]) -> AuthorizationResult:
        """Evaluate OPA policy with given input.

        Args:
            input_data: Policy input including principal, action, resource, context.

        Returns:
            AuthorizationResult from policy evaluation.
            Returns deny on any errors (fail closed).
        """
        try:
            import httpx
        except ImportError:
            logger.error("opa_httpx_not_installed")
            return AuthorizationResult.deny(reason="opa_error:httpx_not_installed")

        url = f"{self._opa_url}/{self._policy_path}"

        try:
            # Lazy initialize client
            if self._client is None:
                self._client = httpx.Client(timeout=self._timeout)

            response = self._client.post(url, json={"input": input_data})
            response.raise_for_status()

            result = response.json()
            allowed = result.get("result", False)

            logger.debug(
                "opa_evaluation_complete",
                url=url,
                allowed=allowed,
                principal_id=input_data.get("principal", {}).get("id"),
            )

            if allowed:
                return AuthorizationResult.allow(reason="opa_policy")
            return AuthorizationResult.deny(reason="opa_denied")

        except httpx.ConnectError as e:
            logger.error("opa_connection_failed", url=url, error=str(e))
            return AuthorizationResult.deny(reason="opa_error:connection_failed")

        except httpx.TimeoutException as e:
            logger.error("opa_timeout", url=url, error=str(e))
            return AuthorizationResult.deny(reason="opa_error:timeout")

        except httpx.HTTPStatusError as e:
            logger.error(
                "opa_http_error",
                url=url,
                status_code=e.response.status_code,
                error=str(e),
            )
            return AuthorizationResult.deny(reason=f"opa_error:http_{e.response.status_code}")

        except Exception as e:
            logger.error("opa_evaluation_failed", url=url, error=str(e))
            return AuthorizationResult.deny(reason=f"opa_error:{type(e).__name__}")

    @staticmethod
    def build_input(request: AuthorizationRequest) -> dict[str, Any]:
        """Build OPA input from authorization request.

        Args:
            request: The authorization request.

        Returns:
            Dictionary formatted for OPA policy input.
        """
        return {
            "principal": {
                "id": request.principal.id.value,
                "type": request.principal.type.value,
                "tenant_id": request.principal.tenant_id,
                "groups": list(request.principal.groups),
                "metadata": request.principal.metadata or {},
            },
            "action": request.action,
            "resource": {
                "type": request.resource_type,
                "id": request.resource_id,
            },
            "context": request.context or {},
        }

    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """Authorize using OPA policy.

        Convenience method that builds input and evaluates policy.

        Args:
            request: The authorization request.

        Returns:
            AuthorizationResult from policy evaluation.
        """
        input_data = self.build_input(request)
        return self.evaluate(input_data)

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> OPAAuthorizer:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class CombinedAuthorizer:
    """Combines RBAC with optional OPA for hybrid authorization.

    Checks RBAC first for performance, then OPA for additional policies.
    Useful when you want fast RBAC for most cases with OPA for complex rules.
    """

    def __init__(
        self,
        rbac_authorizer: RBACAuthorizer,
        opa_authorizer: OPAAuthorizer | None = None,
        require_both: bool = False,
    ):
        """Initialize combined authorizer.

        Args:
            rbac_authorizer: Primary RBAC authorizer.
            opa_authorizer: Optional OPA authorizer for additional checks.
            require_both: If True, both RBAC and OPA must allow.
                         If False, RBAC is checked first and OPA only if RBAC denies.
        """

        self._rbac = rbac_authorizer
        self._opa = opa_authorizer
        self._require_both = require_both

    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """Check authorization with combined strategy.

        Args:
            request: The authorization request.

        Returns:
            AuthorizationResult based on combined evaluation.
        """
        rbac_result = self._rbac.authorize(request)

        if self._opa is None:
            return rbac_result

        if self._require_both:
            # Both must allow
            if not rbac_result.allowed:
                return rbac_result

            opa_result = self._opa.authorize(request)
            if not opa_result.allowed:
                return AuthorizationResult.deny(reason=f"rbac_allowed_but_{opa_result.reason}")

            return AuthorizationResult.allow(
                reason=f"rbac_and_opa_allowed:{rbac_result.matched_role}",
                permission=rbac_result.matched_permission,
                role=rbac_result.matched_role,
            )
        else:
            # RBAC first, OPA as fallback
            if rbac_result.allowed:
                return rbac_result

            # RBAC denied, check OPA
            opa_result = self._opa.authorize(request)
            if opa_result.allowed:
                return AuthorizationResult.allow(
                    reason="opa_override",
                )

            return rbac_result  # Return original RBAC denial
