"""Smoke test for validating provider configuration.

Starts each provider, waits for READY state, reports status, then stops.
Used by `mcp-hangar init` to verify configuration before user closes terminal.
"""

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from ....domain.exceptions import CannotStartProviderError, ProviderStartError
from ....domain.model.provider import Provider
from ....domain.value_objects import ProviderState
from ....logging_config import get_logger
from ....server.config import load_configuration

logger = get_logger(__name__)

# Constants
DEFAULT_TIMEOUT_SECONDS = 10
MAX_PARALLEL_STARTS = 3


@dataclass
class ProviderTestResult:
    """Result of testing a single provider."""

    provider_id: str
    success: bool
    state: str
    duration_ms: float
    error: str | None = None
    suggestion: str | None = None


@dataclass
class SmokeTestResult:
    """Aggregate result of smoke testing all providers."""

    results: list[ProviderTestResult]
    total_duration_ms: float

    @property
    def all_passed(self) -> bool:
        """Check if all providers passed."""
        return all(r.success for r in self.results)

    @property
    def passed_count(self) -> int:
        """Count of passed providers."""
        return sum(1 for r in self.results if r.success)

    @property
    def failed_count(self) -> int:
        """Count of failed providers."""
        return sum(1 for r in self.results if not r.success)


def _test_single_provider(
    provider_id: str,
    provider_config: dict[str, Any],
    timeout_s: float,
) -> ProviderTestResult:
    """Test a single provider by starting it and waiting for READY.

    Args:
        provider_id: Provider identifier.
        provider_config: Provider configuration dict.
        timeout_s: Maximum time to wait for READY state.

    Returns:
        ProviderTestResult with success/failure details.
    """
    start_time = time.perf_counter()

    try:
        # Create provider from config
        provider = Provider(
            provider_id=provider_id,
            mode=provider_config.get("mode", "subprocess"),
            command=provider_config.get("command"),
            image=provider_config.get("image"),
            endpoint=provider_config.get("endpoint"),
            env=provider_config.get("env"),
            idle_ttl_s=provider_config.get("idle_ttl_s", 300),
            health_check_interval_s=provider_config.get("health_check_interval_s", 60),
            volumes=provider_config.get("volumes"),
            resources=provider_config.get("resources"),
            network=provider_config.get("network", "none"),
            auth=provider_config.get("auth"),
            tls=provider_config.get("tls"),
            http=provider_config.get("http"),
        )

        # Start provider with timeout
        deadline = time.time() + timeout_s
        provider.ensure_ready()

        # Wait for READY state (should be immediate after ensure_ready)
        while time.time() < deadline:
            if provider.state == ProviderState.READY:
                duration_ms = (time.perf_counter() - start_time) * 1000

                # Stop provider after successful test
                try:
                    provider.stop()
                except Exception:
                    pass  # Best effort cleanup

                return ProviderTestResult(
                    provider_id=provider_id,
                    success=True,
                    state="ready",
                    duration_ms=duration_ms,
                )
            time.sleep(0.1)

        # Timeout waiting for READY
        duration_ms = (time.perf_counter() - start_time) * 1000
        try:
            provider.stop()
        except Exception:
            pass

        return ProviderTestResult(
            provider_id=provider_id,
            success=False,
            state=str(provider.state.value),
            duration_ms=duration_ms,
            error=f"Timeout after {timeout_s}s - provider did not reach READY state",
            suggestion="Check provider command/image and ensure it starts correctly",
        )

    except ProviderStartError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        return ProviderTestResult(
            provider_id=provider_id,
            success=False,
            state="dead",
            duration_ms=duration_ms,
            error=str(e.reason) if hasattr(e, "reason") else str(e),
            suggestion=e.suggestion if hasattr(e, "suggestion") else _get_suggestion_for_error(str(e)),
        )

    except CannotStartProviderError as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        return ProviderTestResult(
            provider_id=provider_id,
            success=False,
            state="backoff",
            duration_ms=duration_ms,
            error=str(e),
            suggestion="Provider is in backoff state, try again later",
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        return ProviderTestResult(
            provider_id=provider_id,
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
    """Run smoke test on all providers in configuration.

    Args:
        config_path: Path to configuration file.
        timeout_s: Maximum time per provider.
        console: Optional Rich console for output.

    Returns:
        SmokeTestResult with all provider results.
    """
    if console is None:
        console = Console()

    start_time = time.perf_counter()

    # Load configuration
    config = load_configuration(str(config_path))
    providers_config = config.get("providers", {})

    if not providers_config:
        return SmokeTestResult(results=[], total_duration_ms=0)

    results: list[ProviderTestResult] = []
    per_provider_timeout = min(timeout_s / len(providers_config), timeout_s / 2)

    # Run tests with progress indicator
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.completed}/{task.total}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Testing providers...", total=len(providers_config))

        # Test providers sequentially to avoid resource contention
        for provider_id, provider_config in providers_config.items():
            progress.update(task, description=f"Testing {provider_id}...")

            result = _test_single_provider(
                provider_id=provider_id,
                provider_config=provider_config,
                timeout_s=per_provider_timeout,
            )
            results.append(result)

            # Show result immediately
            if result.success:
                console.print(f"  [green]OK[/green] {provider_id} ready ({result.duration_ms:.0f}ms)")
            else:
                console.print(f"  [red]FAIL[/red] {provider_id}: {result.error}")
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
        timeout_s: Maximum time per provider.

    Returns:
        Tuple of (all_passed, [(provider_id, success, error), ...])
    """
    result = run_smoke_test(config_path, timeout_s, console=Console(quiet=True))

    provider_results = [(r.provider_id, r.success, r.error) for r in result.results]

    return result.all_passed, provider_results
