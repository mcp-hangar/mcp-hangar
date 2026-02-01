"""Truncation manager for batch responses.

Orchestrates the truncation of batch responses that exceed size limits,
with smart truncation that preserves JSON structure and line boundaries.
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from ...domain.contracts.response_cache import IResponseCache
from ...domain.value_objects.truncation import ContinuationId, TruncationConfig
from ...logging_config import get_logger
from ...metrics import BATCH_TRUNCATIONS_TOTAL

if TYPE_CHECKING:
    from ...server.tools.batch.models import CallResult

logger = get_logger(__name__)


class TruncationManager:
    """Manages truncation of batch responses.

    Applies proportional budget allocation and smart truncation
    to ensure batch responses fit within configured limits.

    Attributes:
        config: Truncation configuration.
        cache: Response cache for storing full responses.
    """

    def __init__(self, config: TruncationConfig, cache: IResponseCache):
        """Initialize the truncation manager.

        Args:
            config: Truncation configuration.
            cache: Response cache implementation.
        """
        self._config = config
        self._cache = cache

    @property
    def config(self) -> TruncationConfig:
        """Get the truncation configuration."""
        return self._config

    @property
    def cache(self) -> IResponseCache:
        """Get the response cache."""
        return self._cache

    def process_batch(self, batch_id: str, results: list[CallResult]) -> list[CallResult]:
        """Process batch results, applying truncation if needed.

        This is the main entry point. It calculates the total response size,
        and if it exceeds the configured limit, applies proportional truncation
        to each result while caching full responses for later retrieval.

        Args:
            batch_id: Unique identifier for the batch.
            results: List of call results to process.

        Returns:
            List of call results, potentially with truncated content.
        """
        if not self._config.enabled:
            return results

        if not results:
            return results

        # Calculate sizes for each result
        sizes = []
        for r in results:
            if r.result is not None:
                try:
                    size = len(json.dumps(r.result).encode("utf-8"))
                except (TypeError, ValueError):
                    size = 0
            else:
                size = 0
            sizes.append(size)

        total_size = sum(sizes)

        # Check if truncation is needed
        if total_size <= self._config.max_batch_size_bytes:
            logger.debug(
                "batch_no_truncation_needed",
                batch_id=batch_id,
                total_size=total_size,
                limit=self._config.max_batch_size_bytes,
            )
            return results

        logger.info(
            "batch_truncation_required",
            batch_id=batch_id,
            total_size=total_size,
            limit=self._config.max_batch_size_bytes,
            result_count=len(results),
        )

        # Allocate budgets proportionally
        budgets = self._allocate_budgets(sizes)

        # Apply truncation to each result
        truncated_results = []
        for i, (result, budget) in enumerate(zip(results, budgets, strict=False)):
            if sizes[i] > budget:
                truncated_result = self._truncate_result(result, budget, batch_id, i)
                truncated_results.append(truncated_result)
            else:
                truncated_results.append(result)

        return truncated_results

    def _allocate_budgets(self, sizes: list[int]) -> list[int]:
        """Allocate truncation budgets proportionally.

        Each result gets a share of the total budget proportional to its
        original size, with a guaranteed minimum per response.

        Args:
            sizes: List of original response sizes in bytes.

        Returns:
            List of budget allocations in bytes.
        """
        total_size = sum(sizes)
        max_budget = self._config.max_batch_size_bytes
        min_budget = self._config.min_per_response_bytes
        count = len(sizes)

        # Ensure minimum budget doesn't exceed per-response allocation
        min_total = min_budget * count
        if min_total > max_budget:
            # Reduce minimum to fit
            min_budget = max_budget // count

        budgets = []
        for size in sizes:
            if total_size > 0:
                # Proportional allocation
                proportion = size / total_size
                budget = int(max_budget * proportion)
                # Ensure minimum
                budget = max(budget, min_budget)
            else:
                budget = min_budget
            budgets.append(budget)

        # Adjust if total budgets exceed max
        total_budgets = sum(budgets)
        if total_budgets > max_budget:
            scale = max_budget / total_budgets
            budgets = [max(int(b * scale), min_budget) for b in budgets]

        return budgets

    def _truncate_result(
        self,
        result: CallResult,
        budget: int,
        batch_id: str,
        call_index: int,
    ) -> CallResult:
        """Truncate a single result and cache the full response.

        Args:
            result: The call result to truncate.
            budget: Maximum bytes for the truncated result.
            batch_id: Batch identifier.
            call_index: Index of this call in the batch.

        Returns:
            New CallResult with truncated content and continuation_id.
        """
        # Import here to avoid circular import at module load time
        from ...server.tools.batch.models import CallResult

        if result.result is None:
            return result

        # Cache the full response
        continuation_id = ContinuationId.generate(batch_id, call_index)
        self._cache.store(
            continuation_id.value,
            result.result,
            self._config.cache_ttl_s,
        )

        # Calculate original size
        try:
            original_json = json.dumps(result.result)
            original_size = len(original_json.encode("utf-8"))
        except (TypeError, ValueError):
            original_size = 0
            original_json = ""

        # Truncate the result
        if self._config.preserve_json_structure:
            truncated_data = self._smart_truncate_json(result.result, budget)
        else:
            truncated_data = self._simple_truncate(original_json, budget)

        # Record truncation metric
        BATCH_TRUNCATIONS_TOTAL.inc(reason="batch_budget")

        logger.info(
            "result_truncated",
            batch_id=batch_id,
            call_index=call_index,
            original_size=original_size,
            budget=budget,
            continuation_id=continuation_id.value,
        )

        return CallResult(
            index=result.index,
            call_id=result.call_id,
            success=result.success,
            result=truncated_data,
            error=result.error,
            error_type=result.error_type,
            elapsed_ms=result.elapsed_ms,
            truncated=True,
            truncated_reason="batch_budget_exceeded",
            original_size_bytes=original_size,
            retry_metadata=result.retry_metadata,
            continuation_id=continuation_id.value,
        )

    def _smart_truncate_json(self, data: Any, max_bytes: int) -> Any:
        """Truncate data while preserving JSON structure.

        Handles different data types:
        - Strings: Truncate with line boundary awareness
        - Lists/Arrays: Truncate elements from the end
        - Dicts: Keep all keys, truncate values

        Args:
            data: The data to truncate.
            max_bytes: Maximum bytes for the result.

        Returns:
            Truncated data that is valid JSON.
        """
        # First check if it fits
        try:
            serialized = json.dumps(data)
            if len(serialized.encode("utf-8")) <= max_bytes:
                return data
        except (TypeError, ValueError):
            return None

        if isinstance(data, str):
            return self._truncate_string(data, max_bytes)
        elif isinstance(data, list):
            return self._truncate_list(data, max_bytes)
        elif isinstance(data, dict):
            return self._truncate_dict(data, max_bytes)
        else:
            # Primitive types - if they don't fit, return truncation marker
            return "[truncated]"

    def _truncate_string(self, text: str, max_bytes: int) -> str:
        """Truncate a string with optional line boundary awareness.

        Args:
            text: The string to truncate.
            max_bytes: Maximum bytes for the result.

        Returns:
            Truncated string with truncation marker.
        """
        # Account for quotes and truncation marker in JSON
        overhead = len(json.dumps("... [truncated]"))
        available = max_bytes - overhead

        if available <= 0:
            return "[truncated]"

        # Encode to bytes for accurate truncation
        text_bytes = text.encode("utf-8")

        if len(text_bytes) <= available:
            return text

        # Truncate at byte boundary
        truncated_bytes = text_bytes[:available]

        # Decode back to string (may truncate partial characters)
        truncated = truncated_bytes.decode("utf-8", errors="ignore")

        # Optionally truncate at line boundary
        if self._config.truncate_on_line_boundary and "\n" in truncated:
            last_newline = truncated.rfind("\n")
            if last_newline > len(truncated) // 2:  # Only if we keep > 50%
                truncated = truncated[: last_newline + 1]

        return truncated + "... [truncated]"

    def _truncate_list(self, items: list, max_bytes: int) -> list:
        """Truncate a list by removing elements from the end.

        Args:
            items: The list to truncate.
            max_bytes: Maximum bytes for the result.

        Returns:
            Truncated list with truncation marker as last element.
        """
        # Try progressively smaller lists
        for end_index in range(len(items), 0, -1):
            truncated = items[:end_index]
            if end_index < len(items):
                truncated.append(f"[{len(items) - end_index} more items truncated]")

            try:
                serialized = json.dumps(truncated)
                if len(serialized.encode("utf-8")) <= max_bytes:
                    return truncated
            except (TypeError, ValueError):
                continue

        return ["[truncated]"]

    def _truncate_dict(self, data: dict, max_bytes: int) -> dict:
        """Truncate a dictionary by truncating values while keeping keys.

        Args:
            data: The dictionary to truncate.
            max_bytes: Maximum bytes for the result.

        Returns:
            Truncated dictionary.
        """
        # Calculate approximate budget per key-value pair
        num_keys = len(data)
        if num_keys == 0:
            return {}

        # Account for dict overhead (braces, commas, etc.)
        overhead = 2 + (num_keys - 1) * 2  # {} and commas
        available = max_bytes - overhead

        if available <= 0:
            return {"_truncated": True, "_key_count": num_keys}

        budget_per_item = available // num_keys

        result = {}
        for key, value in data.items():
            # Account for key and colon/quotes
            key_overhead = len(json.dumps(key)) + 1  # "key":
            item_budget = budget_per_item - key_overhead

            if item_budget <= 0:
                result[key] = "[truncated]"
            else:
                truncated_value = self._smart_truncate_json(value, item_budget)
                result[key] = truncated_value

        return result

    def _simple_truncate(self, json_str: str, max_bytes: int) -> str:
        """Simple byte-level truncation without structure preservation.

        Args:
            json_str: The JSON string to truncate.
            max_bytes: Maximum bytes for the result.

        Returns:
            Truncated string (may not be valid JSON).
        """
        marker = "... [truncated]"
        available = max_bytes - len(marker.encode("utf-8"))

        if available <= 0:
            return marker

        truncated_bytes = json_str.encode("utf-8")[:available]
        truncated = truncated_bytes.decode("utf-8", errors="ignore")

        return truncated + marker
