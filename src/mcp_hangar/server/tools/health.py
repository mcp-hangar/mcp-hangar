"""Health and metrics tools.

Uses ApplicationContext for dependency injection (DIP).
All operations are QUERY operations - read only, no side effects.
"""

from typing import Any

from mcp_hangar._sdk_compat import FastMCP

from ... import metrics as m
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...logging_config import get_logger
from ...observability.health import get_compliance_export_status
from ..catalogue_readiness import catalogue_detail
from ..context import get_context
from ..validation import not_rate_limited, tool_error_hook, tool_error_mapper
from .replica_view import observe_replica

logger = get_logger(__name__)

# =============================================================================
# Metrics aggregation
# =============================================================================
#
# Read from the named collectors, not by matching substrings of their names. The
# substring rules this replaced never matched what they were written for: no
# metric is called "invocations", so per-server `invocations`, `errors` and
# `avg_latency_ms` stayed 0; `mcp_hangar_tool_call_errors` does not contain
# "tool_calls", so `tool_calls.*.errors` stayed 0; a histogram's bucket, sum and
# count samples were indistinguishable, so `count` took whichever came last; and
# "cycle" matched `discovery_last_cycle_timestamp_seconds`, putting a Unix time in
# `discovery.*.cycles` (#1668).


def _aggregate_tool_calls(result: dict[str, Any]) -> None:
    """Per-tool and per-server call and error counts, and per-server mean latency.

    `mcp_hangar_tool_calls` counts every call with a `status` of success or
    error. Its error samples are the errors; `mcp_hangar_tool_call_errors` breaks
    the same failures down by type and is not added again.
    """
    for sample in m.TOOL_CALLS_TOTAL.collect():
        server = sample.labels.get("mcp_server", "unknown")
        tool = sample.labels.get("tool", "unknown")
        failed = sample.labels.get("status") == "error"
        calls = result["tool_calls"].setdefault(f"{server}.{tool}", {"count": 0, "errors": 0})
        calls["count"] += int(sample.value)
        if failed:
            calls["errors"] += int(sample.value)
        stats = result["mcp_servers"].get(server)
        if stats is not None:
            stats["invocations"] += int(sample.value)
            if failed:
                stats["errors"] += int(sample.value)

    _, sums, counts = m.TOOL_CALL_DURATION_SECONDS.collect()
    total_s: dict[str, float] = {}
    total_n: dict[str, int] = {}
    for sample in sums:
        server = sample.labels.get("mcp_server", "unknown")
        total_s[server] = total_s.get(server, 0.0) + sample.value
    for sample in counts:
        server = sample.labels.get("mcp_server", "unknown")
        total_n[server] = total_n.get(server, 0) + int(sample.value)
    for server, n in total_n.items():
        stats = result["mcp_servers"].get(server)
        if stats is not None and n:
            stats["avg_latency_ms"] = round(total_s.get(server, 0.0) / n * 1000, 3)


def _aggregate_discovery(result: dict[str, Any]) -> None:
    """Discovery cycles by source, and the servers it holds by status."""
    for sample in m.DISCOVERY_CYCLES_TOTAL.collect():
        source = sample.labels.get("source_type") or "unknown"
        entry = result["discovery"].setdefault(source, {})
        entry["cycles"] = entry.get("cycles", 0) + int(sample.value)
    for sample in m.DISCOVERY_PROVIDERS_TOTAL.collect():
        source = sample.labels.get("source_type") or "unknown"
        entry = result["discovery"].setdefault(source, {})
        entry[f"mcp_servers_{sample.labels.get('status', 'total')}"] = int(sample.value)


def _aggregate_errors(result: dict[str, Any]) -> None:
    """Component errors by type, from `mcp_hangar_errors`."""
    for sample in m.ERRORS_TOTAL.collect():
        error_type = sample.labels.get("error_type") or "unknown"
        result["errors"][error_type] = result["errors"].get(error_type, 0) + int(sample.value)


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
    # The compliance (SIEM) file feed: a failing write makes this replica
    # degraded, and the calls it serves go unexported (#1701).
    compliance = get_compliance_export_status()
    if compliance is not None:
        health["compliance_export"] = compliance.report(with_output=True)
        if compliance.failing:
            health["status"] = "degraded"
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

        _aggregate_tool_calls(result)
        _aggregate_discovery(result)
        _aggregate_errors(result)

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
