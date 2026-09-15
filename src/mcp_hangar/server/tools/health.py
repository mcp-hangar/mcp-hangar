"""Health and metrics tools.

Uses ApplicationContext for dependency injection (DIP).
All operations are QUERY operations - read only, no side effects.
"""

from typing import Any

from mcp_hangar._sdk_compat import FastMCP

from ... import metrics as m
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...logging_config import get_logger
from ..catalogue_readiness import catalogue_detail
from ..context import get_context
from ..validation import not_rate_limited, tool_error_hook, tool_error_mapper
from .replica_view import observe_replica

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

    mcp_server = labels.get("mcp_server", "unknown")
    tool = labels.get("tool", "unknown")
    key = f"{mcp_server}.{tool}"

    if key not in tool_calls:
        tool_calls[key] = {"count": 0, "errors": 0}

    if "error" in name:
        tool_calls[key]["errors"] = int(value)
    else:
        tool_calls[key]["count"] = int(value)


def _process_invocations_metric(
    name: str, labels: dict[str, str], value: float, mcp_servers: dict[str, dict[str, Any]]
) -> None:
    """Process invocations metric sample and update mcp_server stats.

    Args:
        name: Metric name.
        labels: Metric labels dict.
        value: Metric value.
        mcp_servers: Dict to accumulate mcp_server invocation counts.
    """
    if "invocations" not in name or "mcp_server" not in labels:
        return

    mcp_server = labels.get("mcp_server")
    if mcp_server and mcp_server in mcp_servers:
        mcp_servers[mcp_server]["invocations"] = int(value)


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
    elif "mcp_servers" in name:
        status = labels.get("status", "total")
        discovery[source][f"mcp_servers_{status}"] = int(value)


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
    _process_invocations_metric(name, labels, value, result["mcp_servers"])
    _process_discovery_metric(name, labels, value, result["discovery"])
    _process_error_metric(name, labels, value, result["errors"])


def hangar_health() -> dict:
    """This replica's server and group health, from the snapshot `hangar_status` reads.

    Rendered from `observe_replica()` rather than a second read of the
    repository. The second read skipped hot-loaded servers, so from a single
    replica `hangar_health` and `hangar_status` could report different totals
    (#1380).
    """
    ctx = get_context()
    view = observe_replica()

    group_state_counts: dict[str, int] = {}
    for group in view.groups:
        group_state_counts[group.state] = group_state_counts.get(group.state, 0) + 1

    health: dict[str, Any] = {
        "status": "healthy",
        "mcp_servers": {
            "total": view.total_servers,
            "by_state": view.servers_by_state(),
        },
        "groups": {
            "total": len(view.groups),
            "by_state": group_state_counts,
            "total_members": sum(g.total_count for g in view.groups),
            "healthy_members": sum(g.healthy_count for g in view.groups),
            "members_in_rotation_count": sum(g.members_in_rotation_count for g in view.groups),
        },
        "security": {
            "rate_limiting": ctx.rate_limiter.get_stats(),
        },
        **view.scope_fields(),
    }
    # The front door's required catalogue, with the ids and dead reasons that
    # `/health/ready` leaves out because it answers without authentication (#1446).
    catalogue = catalogue_detail(ctx.repository)
    if catalogue is not None:
        health["catalogue"] = catalogue
    return health


def register_health_tools(mcp: FastMCP) -> None:
    """Register health and metrics tools with MCP server."""

    @mcp.tool(name="hangar_health")
    @mcp_tool_wrapper(
        tool_name="hangar_health",
        rate_limit_key=key_global,
        check_rate_limit=not_rate_limited,
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def _hangar_health() -> dict:
        """Get registry health status including security metrics.

        CHOOSE THIS when: quick health check, monitoring dashboard, security overview.
        CHOOSE hangar_metrics when: detailed per-mcp_server stats, tool call counts, Prometheus.
        CHOOSE hangar_status when: human-readable dashboard with visual indicators.

        SCOPE: replica-local. The answer describes the replica that served this
        call, named in replica.instance_id, not the whole fleet. Another replica
        can answer differently at the same moment. Reads the same snapshot as
        hangar_status, so the two tools agree when one replica answers both.

        Side effects: None (read-only).

        Args:
            None

        Returns:
            {
                status: str,
                mcp_servers: {total: int, by_state: {cold: int, ready: int, degraded: int, dead: int}},
                groups: {
                    total: int,
                    by_state: object,
                    total_members: int,
                    healthy_members: int,
                    members_in_rotation_count: int
                },
                security: {rate_limiting: {active_buckets: int, config: object}},
                replica: {instance_id: str, uptime_seconds: float, uptime: str},
                scope: "replica",
                scope_note: str
            }
            mcp_servers counts configured and hot-loaded servers on this replica.
            groups sums every group's healthy_count (members ready and in
            rotation) and members_in_rotation_count (members in rotation in any
            state), as hangar_group_list reports them.

        Example:
            hangar_health()
            # {"status": "healthy",
            #  "mcp_servers": {"total": 3, "by_state": {"ready": 2, "cold": 1}},
            #  "groups": {"total": 1, "by_state": {"ready": 1}, "total_members": 3, "healthy_members": 2,
            #             "members_in_rotation_count": 2},
            #  "security": {"rate_limiting": {"active_buckets": 5, "config": {...}}},
            #  "replica": {"instance_id": "hangar-0-3fa81c2e", "uptime_seconds": 8100.0, "uptime": "2h 15m"},
            #  "scope": "replica", "scope_note": "This describes what the replica named ..."}
        """
        return hangar_health()

    @mcp.tool(name="hangar_metrics")
    @mcp_tool_wrapper(
        tool_name="hangar_metrics",
        rate_limit_key=key_global,
        check_rate_limit=not_rate_limited,
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_metrics(format: str = "json") -> dict:
        """Get detailed metrics for mcp_servers, groups, and system components.

        CHOOSE THIS when: debugging, performance analysis, Prometheus scraping, tool call stats.
        CHOOSE hangar_health when: quick health check with security metrics.
        CHOOSE hangar_status when: human-readable overview for display.

        Side effects: None (read-only).

        Args:
            format: str - Output format: "json" or "prometheus" (default: "json")

        Returns:
            JSON format: {
                mcp_servers: {<id>: {state, mode, tools_count, invocations, errors, avg_latency_ms}},
                groups: {<id>: {state, strategy, total_members, healthy_members, members_in_rotation_count}},
                tool_calls: {<mcp_server.tool>: {count, errors}},
                discovery: object,
                errors: {<type>: int},
                performance: object,
                summary: {total_mcp_servers, total_groups, total_tool_calls, total_errors}
            }
            Prometheus format: {metrics: str}

        Example:
            hangar_metrics()
            # {"mcp_servers": {"math": {"state": "ready", "mode": "subprocess", "invocations": 42}},
            #  "tool_calls": {"math.add": {"count": 30, "errors": 0}},
            #  "summary": {"total_mcp_servers": 1, "total_tool_calls": 42, "total_errors": 0}}

            hangar_metrics(format="prometheus")
            # {"metrics": "# HELP mcp_hangar_tool_calls_total ...\\nmcp_hangar_tool_calls_total{...} 42"}
        """
        ctx = get_context()

        if format == "prometheus":
            return {"metrics": m.REGISTRY.get_metrics_output()}

        result: dict[str, Any] = {
            "mcp_servers": {},
            "groups": {},
            "tool_calls": {},
            "discovery": {},
            "errors": {},
            "performance": {},
        }

        # McpServer metrics via repository
        all_mcp_servers = ctx.repository.get_all()
        for mcp_server in all_mcp_servers.values():
            pid = mcp_server.mcp_server_id
            result["mcp_servers"][pid] = {
                "state": str(mcp_server.state),
                "mode": mcp_server._mode.value if hasattr(mcp_server, "_mode") else "unknown",
                "tools_count": len(mcp_server.tools) if mcp_server.tools else 0,
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
            result["groups"][group.id] = {
                "state": group.state.value,
                "strategy": group.strategy.value if hasattr(group.strategy, "value") else str(group.strategy),
                "total_members": group.total_count,
                "healthy_members": group.healthy_count,
                "members_in_rotation_count": group.members_in_rotation_count,
            }

        # Summary stats
        result["summary"] = {
            "total_mcp_servers": len(result["mcp_servers"]),
            "total_groups": len(result["groups"]),
            "total_tool_calls": sum(tc.get("count", 0) for tc in result["tool_calls"].values()),
            "total_errors": sum(result["errors"].values()),
        }

        return result
