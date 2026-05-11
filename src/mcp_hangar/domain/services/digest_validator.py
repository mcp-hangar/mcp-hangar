"""Standalone digest validator for non-cloud mode (SEP-1766).

Validates tool digests against an in-process allowlist defined by DigestPolicy.
Produces DigestMismatchEvent when observed digest differs from expected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_hangar.domain.events import DigestMismatchEvent
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.value_objects.tool_digest import (
    DigestEnforcement,
    DigestPolicy,
    DigestUnknownPolicy,
)


@dataclass(frozen=True)
class DigestValidationResult:
    """Result of validating a single tool's digest against the policy."""

    tool_name: str
    valid: bool
    blocked: bool
    event: DigestMismatchEvent | None


class DigestValidator:
    """Validates tool digests against a DigestPolicy allowlist.

    Stateless service: instantiate with a policy, call validate_tool() per tool.
    Emits DigestMismatchEvent objects for each mismatch (caller publishes them).
    """

    def __init__(self, policy: DigestPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> DigestPolicy:
        return self._policy

    def validate_tool(
        self,
        tool: dict[str, Any],
        mcp_server_id: str,
        correlation_id: str,
    ) -> DigestValidationResult:
        """Validate a single tool schema against the digest policy.

        Args:
            tool: Tool schema dict (from tools/list response).
            mcp_server_id: Identifier of the MCP server providing the tool.
            correlation_id: Request correlation ID for audit trail.

        Returns:
            DigestValidationResult with validation outcome and optional event.
        """
        observed = compute_tool_digest(tool)
        expected = self._policy.get_expected_digest(observed.tool_name)

        if expected is None:
            return self._handle_unknown_tool(observed.tool_name, observed.sha256, mcp_server_id, correlation_id)

        if expected.sha256 == observed.sha256:
            return DigestValidationResult(tool_name=observed.tool_name, valid=True, blocked=False, event=None)

        return self._handle_mismatch(
            tool_name=observed.tool_name,
            expected_digest=expected.sha256,
            observed_digest=observed.sha256,
            mcp_server_id=mcp_server_id,
            correlation_id=correlation_id,
        )

    def validate_tool_list(
        self,
        tools: list[dict[str, Any]],
        mcp_server_id: str,
        correlation_id: str,
    ) -> list[DigestValidationResult]:
        """Validate all tools from a tools/list response.

        Returns a list of results, one per tool. Only tools with issues
        will have event != None.
        """
        return [self.validate_tool(tool, mcp_server_id, correlation_id) for tool in tools]

    def _handle_unknown_tool(
        self,
        tool_name: str,
        observed_digest: str,
        mcp_server_id: str,
        correlation_id: str,
    ) -> DigestValidationResult:
        unknown_policy = self._policy.unknown

        if unknown_policy == DigestUnknownPolicy.ALLOW_UNVERIFIED:
            return DigestValidationResult(tool_name=tool_name, valid=True, blocked=False, event=None)

        event = DigestMismatchEvent(
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            expected_digest=None,
            observed_digest=observed_digest,
            enforcement=unknown_policy.value,
            correlation_id=correlation_id,
        )

        blocked = unknown_policy == DigestUnknownPolicy.BLOCK
        return DigestValidationResult(tool_name=tool_name, valid=False, blocked=blocked, event=event)

    def _handle_mismatch(
        self,
        tool_name: str,
        expected_digest: str,
        observed_digest: str,
        mcp_server_id: str,
        correlation_id: str,
    ) -> DigestValidationResult:
        enforcement = self._policy.enforcement

        event = DigestMismatchEvent(
            mcp_server_id=mcp_server_id,
            tool_name=tool_name,
            expected_digest=expected_digest,
            observed_digest=observed_digest,
            enforcement=enforcement.value,
            correlation_id=correlation_id,
        )

        blocked = enforcement == DigestEnforcement.BLOCK
        return DigestValidationResult(tool_name=tool_name, valid=False, blocked=blocked, event=event)
