"""Health and metrics tools.

Uses ApplicationContext for dependency injection (DIP).
All operations are QUERY operations - read only, no side effects.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from ... import metrics as m
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...logging_config import get_logger
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper

logger = get_logger(__name__)

# =============================================================================
# Metrics Processing Helpers
# =============================================================================


def _collect_samples_from_collector(collector: Any) -> list[Any]:
    """Extract metric samples from a Prometheus collector.

    Args:
        collector: Prometheus metric collector instance.

    Returns:
        List of metric samples extracted from the collector.
    """
    if not hasattr(collector, "collect"):
        return []

    collected = collector.collect()
    if isinstance(collected, list):
        return collected

    if isinstance(collected, tuple):
        samples = []
        for item in collected:
            if isinstance(item, list):
                samples.extend(item)
            elif hasattr(item, "labels"):
                samples.append(item)
        return samples

    return []


def _process_tool_calls_metric(
    name: str, labels: dict[str, str], value: float, tool_calls: dict[str, dict[str, int]]
) -> None:
    """Process tool_calls metric sample and update aggregation dict.

    Args:
        name: Metric name.
        labels: Metric labels dict.
        value: Metric value.
        tool_calls: Dict to accumulate tool call counts.
    """
    if "tool_calls" not in name:
        return

    provider = labels.get("provider", "unknown")
    tool = labels.get("tool", "unknown")
    key = f"{provider}.{tool}"

    if key not in tool_calls:
        tool_calls[key] = {"count": 0, "errors": 0}

    if "error" in name:
        tool_calls[key]["errors"] = int(value)
    else:
        tool_calls[key]["count"] = int(value)


def _process_invocations_metric(
    name: str, labels: dict[str, str], value: float, providers: dict[str, dict[str, Any]]
) -> None:
    """Process invocations metric sample and update provider stats.

    Args:
        name: Metric name.
        labels: Metric labels dict.
        value: Metric value.
        providers: Dict to accumulate provider invocation counts.
    """
    if "invocations" not in name or "provider" not in labels:
        return

    provider = labels.get("provider")
    if provider and provider in providers:
        providers[provider]["invocations"] = int(value)


def _process_discovery_metric(
    name: str, labels: dict[str, str], value: float, discovery: dict[str, dict[str, Any]]
) -> None:
    """Process discovery metric sample and update discovery stats.

    Args:
        name: Metric name.
        labels: Metric labels dict.
        value: Metric value.
        discovery: Dict to accumulate discovery statistics.
    """
    if "discovery" not in name:
        return

    source = labels.get("source_type", labels.get("source", "unknown"))
    if not source:
        return

    if source not in discovery:
        discovery[source] = {}

    if "cycle" in name:
        discovery[source]["cycles"] = int(value)
    elif "providers" in name:
        status = labels.get("status", "total")
        discovery[source][f"providers_{status}"] = int(value)


def _process_error_metric(name: str, labels: dict[str, str], value: float, errors: dict[str, int]) -> None:
    """Process error metric sample and update error counts.

    Args:
        name: Metric name.
        labels: Metric labels dict.
        value: Metric value.
        errors: Dict to accumulate error counts by type.
    """
    if "error" not in name.lower():
        return

    error_type = labels.get("error_type", labels.get("type", name))
    errors[error_type] = errors.get(error_type, 0) + int(value)


def _process_metric_sample(sample: Any, result: dict[str, Any]) -> None:
    """Process a single metric sample and update result dict.

    Routes the sample to appropriate processor based on metric name.

    Args:
        sample: Metric sample with labels and value attributes.
        result: Result dict to update with processed metrics.
    """
    if not hasattr(sample, "labels") or not hasattr(sample, "value"):
        return

    labels = sample.labels or {}
    value = sample.value
    name = getattr(sample, "name", "")

    _process_tool_calls_metric(name, labels, value, result["tool_calls"])
    _process_invocations_metric(name, labels, value, result["providers"])
    _process_discovery_metric(name, labels, value, result["discovery"])
    _process_error_metric(name, labels, value, result["errors"])


def register_health_tools(mcp: FastMCP) -> None:
    """Register health and metrics tools with MCP server."""

    @mcp.tool(name="registry_health")
    @mcp_tool_wrapper(
        tool_name="registry_health",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_health"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def registry_health() -> dict:
        """
        Get registry health status including security metrics.

        This is a QUERY operation - read only.

        Returns:
            Dictionary with health information
        """
        ctx = get_context()
        rate_limit_stats = ctx.rate_limiter.get_stats()

        # Get all providers via repository
        all_providers = ctx.repository.get_all()
        providers = list(all_providers.values())
        state_counts = {}
        for p in providers:
            state = str(p.state)
            state_counts[state] = state_counts.get(state, 0) + 1

        group_state_counts = {}
        total_group_members = 0
        healthy_group_members = 0
        for group in ctx.groups.values():
            state = group.state.value
            group_state_counts[state] = group_state_counts.get(state, 0) + 1
            total_group_members += group.total_count
            healthy_group_members += group.healthy_count

        return {
            "status": "healthy",
            "providers": {
                "total": len(providers),
                "by_state": state_counts,
            },
            "groups": {
                "total": len(ctx.groups),
                "by_state": group_state_counts,
                "total_members": total_group_members,
                "healthy_members": healthy_group_members,
            },
            "security": {
                "rate_limiting": rate_limit_stats,
            },
        }

    @mcp.tool(name="registry_metrics")
    @mcp_tool_wrapper(
        tool_name="registry_metrics",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_metrics"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def registry_metrics(format: str = "json") -> dict:
        """
        Get detailed metrics for all providers, groups, and system components.

        This is a QUERY operation - read only.

        Args:
            format: Output format - 'json' (structured) or 'prometheus' (raw text)

        Returns:
            Dictionary with comprehensive metrics
        """
        ctx = get_context()

        if format == "prometheus":
            return {"metrics": m.REGISTRY.render()}

        result: dict[str, Any] = {
            "providers": {},
            "groups": {},
            "tool_calls": {},
            "discovery": {},
            "errors": {},
            "performance": {},
        }

        # Provider metrics via repository
        all_providers = ctx.repository.get_all()
        for provider in all_providers.values():
            pid = provider.provider_id
            result["providers"][pid] = {
                "state": str(provider.state),
                "mode": provider._mode.value if hasattr(provider, "_mode") else "unknown",
                "tools_count": len(provider.tools) if provider.tools else 0,
                "invocations": 0,
                "errors": 0,
                "avg_latency_ms": 0,
            }

        # Collect metrics from registry
        for name, collector in m.REGISTRY._collectors.items():
            try:
                samples = _collect_samples_from_collector(collector)
                for sample in samples:
                    # Add collector name to sample for processing
                    if not hasattr(sample, "name"):
                        sample.name = name
                    _process_metric_sample(sample, result)
            except (AttributeError, TypeError, ValueError) as e:
                # Skip malformed collectors gracefully
                logger.debug("metrics_collector_error", collector=name, error=str(e))
                continue

        # Group metrics
        for group in ctx.groups.values():
            result["groups"][group.name] = {
                "state": group.state.value,
                "strategy": group.strategy,
                "total_members": group.total_count,
                "healthy_members": group.healthy_count,
            }

        # Summary stats
        result["summary"] = {
            "total_providers": len(result["providers"]),
            "total_groups": len(result["groups"]),
            "total_tool_calls": sum(tc.get("count", 0) for tc in result["tool_calls"].values()),
            "total_errors": sum(result["errors"].values()),
        }

        return result
