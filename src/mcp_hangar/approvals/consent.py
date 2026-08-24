"""The consent provider behind a ``ui://`` delivery (#1048, ADR-024).

SEP-1865 mandates a human decision before a ``ui://`` resource is rendered in a
client webview. :class:`~mcp_hangar.domain.services.ui_resource_guard.UiResourceGuard`
states that mandate and refuses without it; this is the adapter that lets it be
satisfied, over the approval gate that already exists rather than a second hold
mechanism beside it.

Why the subject slot carries a URI. An ``ApprovalRequest`` names a tool and an
``arguments_hash``, and ADR-024 declines to grow it a kind: the record's shape is
the one being proposed upstream (ADR-017), and forking it for one case is a worse
trade than reusing the slot. So a consent request records the resource URI where
a tool name goes, with no arguments -- a fetch has none. The rendered line an
approver sees, "Approval required: ui://reports/q3 on analytics", says what is
about to be delivered and by whom, which is the question they are answering.

Fail-closed in the same three ways the guard is: a denial, a timeout and an
error all return False, and the guard turns any of them into a resource the
client is told does not exist.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


class ApprovalConsentGate:
    """A :class:`UiConsentGate` backed by :class:`ApprovalGateService`."""

    def __init__(self, approval_service: Any) -> None:
        self._approvals = approval_service

    async def request_consent(
        self,
        uri: str,
        tenant_id: str | None,
        mcp_server_id: str,
        correlation_id: str,
    ) -> bool:
        """Hold the delivery until a human decides. False on anything else.

        The policy is synthesised per call rather than read from the resolver:
        the resolver answers about tools, and this consent is mandated by the
        resource's scheme, not by an operator's pattern. ``approval_list``
        carrying the URI is what makes ``requires_approval()`` true for it.
        """
        result = await self._approvals.check(
            tool_name=uri,
            arguments={},
            policy=ToolAccessPolicy(approval_list=(uri,)),
            correlation_id=correlation_id,
            mcp_server_id=mcp_server_id,
            tenant_id=tenant_id,
        )
        approved = bool(getattr(result, "approved", False))
        if not approved:
            logger.info(
                "ui_resource_consent_refused",
                uri=uri,
                tenant_id=tenant_id,
                mcp_server_id=mcp_server_id,
                reason=getattr(result, "reason", None) or getattr(result, "error_code", None),
            )
        return approved
