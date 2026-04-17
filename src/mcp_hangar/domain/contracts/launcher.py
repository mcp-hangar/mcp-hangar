"""Launcher contracts for provider startup infrastructure."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcp_hangar.http_client import HttpClient
from mcp_hangar.stdio_client import StdioClient

LaunchResult = StdioClient | HttpClient


@runtime_checkable
class IProviderLauncher(Protocol):
    """Structural contract for infrastructure launchers."""

    def launch(self, *args: object, **kwargs: object) -> LaunchResult:
        """Launch a provider transport client from provider config."""
        ...

    def stop(self, provider_id: str) -> None:
        """Stop a launched provider, if the launcher tracks it."""
        ...
