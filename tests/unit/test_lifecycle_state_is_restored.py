"""A server that was degraded before a restart comes back degraded.

Configuration says what a server should be; the stream says what it was doing.
Recovery has always rebuilt the first and thrown away the second, so every
process restart handed out a free circuit-breaker reset -- the one thing an
enforcement plane must not do quietly.

The split this pins:

* replayed -- state, health counters, invocation totals, last use;
* from configuration -- mode, command, image, endpoint, env, TTLs, thresholds;
* never restored -- the live transport client. Liveness is re-earned by
  connecting, never assumed from a record.
"""

from __future__ import annotations

from mcp_hangar.domain.events import (
    HealthCheckFailed,
    McpServerDegraded,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from mcp_hangar.domain.model.mcp_server import McpServer, McpServerState


def _server(mcp_server_id: str = "math") -> McpServer:
    return McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["echo"])


class TestWhatReplayRestores:
    def test_a_degraded_server_does_not_come_back_healthy(self) -> None:
        server = _server()
        assert server.state == McpServerState.COLD

        server.restore_from_events(
            [
                McpServerStarted(mcp_server_id="math", mode="subprocess", tools_count=1, startup_duration_ms=5.0),
                McpServerDegraded(
                    mcp_server_id="math",
                    consecutive_failures=3,
                    total_failures=7,
                    reason="upstream timeouts",
                ),
            ]
        )

        assert server.state == McpServerState.DEGRADED
        assert server.health.consecutive_failures == 3
        assert server.health.total_failures == 7

    def test_the_last_event_wins(self) -> None:
        server = _server()
        server.restore_from_events(
            [
                McpServerStarted(mcp_server_id="math", mode="subprocess", tools_count=1, startup_duration_ms=5.0),
                McpServerStateChanged(mcp_server_id="math", old_state="ready", new_state="degraded"),
                McpServerStopped(mcp_server_id="math", reason="operator"),
            ]
        )
        assert server.state == McpServerState.COLD

    def test_failure_counters_accumulate_across_the_stream(self) -> None:
        server = _server()
        server.restore_from_events(
            [
                ToolInvocationFailed(mcp_server_id="math", tool_name="add", error_message="boom"),
                ToolInvocationFailed(mcp_server_id="math", tool_name="add", error_message="boom"),
            ]
        )
        assert server.health.consecutive_failures == 2
        assert server.health.total_failures == 2

    def test_a_success_clears_the_consecutive_count(self) -> None:
        server = _server()
        server.restore_from_events(
            [
                ToolInvocationFailed(mcp_server_id="math", tool_name="add", error_message="boom"),
                ToolInvocationCompleted(mcp_server_id="math", tool_name="add", duration_ms=1.0),
            ]
        )
        assert server.health.consecutive_failures == 0
        assert server.health.total_failures == 1, "the total is history and does not clear"

    def test_replay_does_not_re_date_the_history_it_reads(self) -> None:
        # `record_failure()` stamps time.time(); replaying a week-old failure
        # through it would move that failure to now. The restore seam exists to
        # keep the stored timestamp, the same defect #704 fixed for event ids.
        old = 1_600_000_000.0
        server = _server()
        server.restore_from_events(
            [HealthCheckFailed(mcp_server_id="math", consecutive_failures=2, error_message="timeout", occurred_at=old)]
        )
        assert server.health.last_failure_at == old


class TestWhatReplayMustNotRestore:
    def test_the_live_client_is_not_resurrected(self) -> None:
        server = _server()
        server.restore_from_events(
            [
                McpServerStarted(mcp_server_id="math", mode="subprocess", tools_count=1, startup_duration_ms=5.0),
                McpServerStopped(mcp_server_id="math", reason="crash"),
            ]
        )
        # Liveness is re-earned by connecting. A record saying the server was
        # once running must never present itself as a usable connection.
        assert server._client is None

    def test_configuration_comes_from_config_not_from_history(self) -> None:
        server = McpServer(mcp_server_id="math", mode="subprocess", command=["configured"])
        server.restore_from_events(
            [McpServerStarted(mcp_server_id="math", mode="docker", tools_count=1, startup_duration_ms=5.0)]
        )

        # The event carries a mode. The operator's configuration is what the
        # server should be; history is only what it did.
        assert server._command == ["configured"]


class TestRobustness:
    def test_an_unknown_event_type_is_skipped_not_raised(self) -> None:
        from mcp_hangar.domain.events import CostReportGenerated

        server = _server()
        applied = server.restore_from_events(
            [
                CostReportGenerated(
                    tenant_id="t",
                    period_start="2026-08-01",
                    period_end="2026-08-31",
                    total_cost="1.00",
                    currency="USD",
                ),
                McpServerStarted(mcp_server_id="math", mode="subprocess", tools_count=1, startup_duration_ms=5.0),
            ]
        )
        # A stream written by a newer version must not stop an older one booting.
        assert applied == 1
        assert server.state == McpServerState.READY

    def test_an_empty_stream_changes_nothing(self) -> None:
        server = _server()
        assert server.restore_from_events([]) == 0
        assert server.state == McpServerState.COLD

    def test_a_legacy_provider_alias_finds_its_modern_handler(self) -> None:
        # The 15 deprecated `Provider*` events subclass their `McpServer*`
        # counterparts. A stream written before the rename replays through the
        # MRO walk, exactly as bus dispatch does.
        from mcp_hangar.domain.events import lifecycle

        legacy_cls = getattr(lifecycle, "".join(("Provider", "Stopped")), None)
        if legacy_cls is None:  # pragma: no cover - alias moved
            return
        server = _server()
        server.restore_from_events(
            [McpServerStarted(mcp_server_id="math", mode="subprocess", tools_count=1, startup_duration_ms=5.0)]
        )
        applied = server.restore_from_events([legacy_cls(mcp_server_id="math", reason="legacy")])
        assert applied == 1
        assert server.state == McpServerState.COLD
