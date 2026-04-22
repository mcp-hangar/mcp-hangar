"""Smoke test for validating mcp_server configuration.

Starts each mcp_server, waits for READY state, reports status, then stops.
Used by `mcp-hangar init` to verify configuration before user closes terminal.
"""

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from ....domain.exceptions import CannotStartMcpServerError, McpServerStartError
from ....domain.model.mcp_server import McpServer
from ....domain.value_objects import McpServerState
from ....logging_config import get_logger
from ....server.config import load_configuration

logger = get_logger(__name__)

# legacy alias for tests patching the old symbol
Provider = McpServer
ProviderState = McpServerState

# Constants
DEFAULT_TIMEOUT_SECONDS = 10
MAX_PARALLEL_STARTS = 3


@dataclass
class McpServerTestResult:
    """Result of testing a single mcp_server."""

    mcp_server_id: str
    success: bool
    state: str
    duration_ms: float
    error: str | None = None
    suggestion: str | None = None


@dataclass
class SmokeTestResult:
    """Aggregate result of smoke testing all mcp_servers."""

    results: list[McpServerTestResult]
    total_duration_ms: float

    @property
    def all_passed(self) -> bool:
        """Check if all mcp_servers passed."""
        return all(r.success for r in self.results)

    @property
    def passed_count(self) -> int:
        """Count of passed mcp_servers."""
        return sum(1 for r in self.results if r.success)

    @property
    def failed_count(self) -> int:
        """Count of failed mcp_servers."""
        return sum(1 for r in self.results if not r.success)


def _test_single_mcp_server(
    mcp_server_id: str,
    mcp_server_config: dict[str, Any],
    timeout_s: float,
) -> McpServerTestResult:
    """Test a single mcp_server by starting it and waiting for READY.

    Args:
        mcp_server_id: McpServer identifier.
        mcp_server_config: McpServer configuration dict.
        timeout_s: Maximum time to wait for READY state.

    Returns:
        McpServerTestResult with success/failure details.
    """
    start_time = time.perf_counter()

    try:
        # Create mcp_server from config
        mcp_server = McpServer(
            mcp_server_id=mcp_server_id,
            mode=mcp_server_config.get("mode", "subprocess"),
            command=mcp_server_config.get("command"),
            image=mcp_server_config.get("image"),
            endpoint=mcp_server_config.get("endpoint"),
            env=mcp_server_config.get("env"),
            idle_ttl_s=mcp_server_config.get("idle_ttl_s", 300),
            health_check_interval_s=mcp_server_config.get("health_check_interval_s", 60),
            volumes=mcp_server_config.get("volumes"),
            resources=mcp_server_config.get("resources"),
            network=mcp_server_config.get("network", "none"),
            auth=mcp_server_config.get("auth"),
            tls=mcp_server_config.get("tls"),
            http=mcp_server_config.get("http"),
        )

        # Start mcp_server with timeout
        deadline = time.time() + timeout_s
        mcp_server.ensure_ready()

        # Wait for READY state (should be immediate after ensure_ready)
        while time.time() < deadline:
            if mcp_server.state == McpServerState.READY:
                duration_ms = (time.perf_counter() - start_time) * 1000

                # Stop mcp_server after successful test
                try:
                    mcp_server.stop()
                except Exception:  # noqa: BLE001 -- fault-barrier: best-effort cleanup after successful test
                    pass  # Best effort cleanup

                return McpServerTestResult(
                    mcp_server_id=mcp_server_id,
                    success=True,
                    state="ready",
                    duration_ms=duration_ms,
                )
            time.sleep(0.1)

        # Timeout waiting for READY
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            mcp_server.stop()
        except Exception:  # noqa: BLE001 -- fault-barrier: best-effort cleanup after timeout
            pass

        return McpServerTestResult(
            mcp_server_id=mcp_server_id,
            success=False,
            state=str(mcp_server.state.value),
            duration_ms=duration_ms,
            error=f"Timeout after {timeout_s}s - mcp_server did not reach READY state",
            suggestion="Check mcp_server command/image and ensure it starts correctly",
        )

    except McpServerStartError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        return McpServerTestResult(
            mcp_server_id=mcp_server_id,
            success=False,
            state="dead",
            duration_ms=duration_ms,
            error=str(e.reason) if hasattr(e, "reason") else str(e),
            suggestion=e.suggestion if hasattr(e, "suggestion") else _get_suggestion_for_error(str(e)),
        )

    except CannotStartMcpServerError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        return McpServerTestResult(
            mcp_server_id=mcp_server_id,
            success=False,
            state="backoff",
            duration_ms=duration_ms,
            error=str(e),
            suggestion="McpServer is in backoff state, try again later",
        )

    except Exception as e:  # noqa: BLE001 -- fault-barrier: smoke test must return result, not crash CLI
        duration_ms = (time.perf_counter() - start_time) * 1000
        return McpServerTestResult(
            mcp_server_id=mcp_server_id,
            success=False,
            state="error",
            duration_ms=duration_ms,
            error=str(e),
            suggestion=_get_suggestion_for_error(str(e)),
        )


def _get_suggestion_for_error(error: str) -> str | None:
    """Get actionable suggestion based on error message."""
    error_lower = error.lower()

    if "command not found" in error_lower or "no such file" in error_lower:
        return "Check that the command/binary exists and is in PATH"
    if "permission denied" in error_lower:
        return "Check file permissions or run with appropriate privileges"
    if "connection refused" in error_lower or "connection timed out" in error_lower:
        return "Check that the remote endpoint is accessible"
    if "image not found" in error_lower or "pull" in error_lower:
        return "Check that the Docker image exists and is accessible"
    if "modulenotfounderror" in error_lower or "importerror" in error_lower:
        return "Install missing Python dependencies"
    if "enoent" in error_lower:
        return "File or directory not found - check paths in configuration"

    return None


def run_smoke_test(
    config_path: Path,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    console: Console | None = None,
) -> SmokeTestResult:
    """Run smoke test on all mcp_servers in configuration.

    Args:
        config_path: Path to configuration file.
        timeout_s: Maximum time per mcp_server.
        console: Optional Rich console for output.

    Returns:
        SmokeTestResult with all mcp_server results.
    """
    if console is None:
        console = Console()

    start_time = time.perf_counter()

    # Load configuration
    config = load_configuration(str(config_path))
    mcp_servers_config = config.get("mcp_servers", {})

    if not mcp_servers_config:
        return SmokeTestResult(results=[], total_duration_ms=0)

    results: list[McpServerTestResult] = []
    per_mcp_server_timeout = min(timeout_s / len(mcp_servers_config), timeout_s / 2)

    # Run tests with progress indicator
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.completed}/{task.total}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Testing mcp_servers...", total=len(mcp_servers_config))

        # Test mcp_servers sequentially to avoid resource contention
        for mcp_server_id, mcp_server_config in mcp_servers_config.items():
            progress.update(task, description=f"Testing {mcp_server_id}...")

            result = _test_single_mcp_server(
                mcp_server_id=mcp_server_id,
                mcp_server_config=mcp_server_config,
                timeout_s=per_mcp_server_timeout,
            )
            results.append(result)

            # Show result immediately
            if result.success:
                console.print(f"  [green]OK[/green] {mcp_server_id} ready ({result.duration_ms:.0f}ms)")
            else:
                console.print(f"  [red]FAIL[/red] {mcp_server_id}: {result.error}")
                if result.suggestion:
                    console.print(f"       [dim]Suggestion: {result.suggestion}[/dim]")

            progress.advance(task)

    total_duration_ms = (time.perf_counter() - start_time) * 1000

    return SmokeTestResult(
        results=results,
        total_duration_ms=total_duration_ms,
    )


def run_smoke_test_simple(
    config_path: Path,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[bool, list[tuple[str, bool, str | None]]]:
    """Simplified smoke test returning basic results.

    Args:
        config_path: Path to configuration file.
        timeout_s: Maximum time per mcp_server.

    Returns:
        Tuple of (all_passed, [(mcp_server_id, success, error), ...])
    """
    result = run_smoke_test(config_path, timeout_s, console=Console(quiet=True))

    mcp_server_results = [(r.mcp_server_id, r.success, r.error) for r in result.results]

    return result.all_passed, mcp_server_results


# legacy aliases
_test_single_provider = _test_single_mcp_server

ProviderTestResult = McpServerTestResult
