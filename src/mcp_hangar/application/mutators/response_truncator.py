"""ResponseTruncator: truncates oversized tools/call response payloads.

Emits a ResponseTruncated domain event when truncation occurs.
Builds on existing truncation infrastructure per ADR-005 design choice.
"""

from __future__ import annotations

import json
from typing import Any

from mcp_hangar.domain.contracts.mutator import MutationContext, MutationResult
from mcp_hangar.domain.events import ResponseTruncated
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_BYTES = 900_000
_APPLIES_TO = frozenset({"tools/call"})


class ResponseTruncator:
    """Truncates tools/call response payloads exceeding max_bytes.

    Only applies to response-direction payloads. Truncates the ``result``
    field of the payload dict if its serialized size exceeds the limit.
    """

    def __init__(
        self,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        event_collector: list[Any] | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._event_collector = event_collector

    @property
    def priority_hint(self) -> int:
        return 1000

    @property
    def applies_to(self) -> frozenset[str]:
        return _APPLIES_TO

    def mutate(self, context: MutationContext) -> MutationResult:
        if context.direction != "response":
            return MutationResult(payload=context.payload, changed=False)

        result_value = context.payload.get("result")
        if result_value is None:
            return MutationResult(payload=context.payload, changed=False)

        serialized = json.dumps(result_value, separators=(",", ":"))
        original_size = len(serialized.encode("utf-8"))

        if original_size <= self._max_bytes:
            return MutationResult(payload=context.payload, changed=False)

        truncated = serialized[: self._max_bytes].encode("utf-8")[: self._max_bytes].decode("utf-8", errors="ignore")
        truncated_size = len(truncated.encode("utf-8"))

        new_payload = {**context.payload, "result": truncated}

        event = ResponseTruncated(
            method=context.method,
            correlation_id=context.correlation_id,
            original_size=original_size,
            truncated_size=truncated_size,
            max_size=self._max_bytes,
        )

        if self._event_collector is not None:
            self._event_collector.append(event)

        logger.info(
            "response_truncated",
            method=context.method,
            correlation_id=context.correlation_id,
            original_size=original_size,
            truncated_size=truncated_size,
            max_size=self._max_bytes,
        )

        return MutationResult(payload=new_payload, changed=True)
