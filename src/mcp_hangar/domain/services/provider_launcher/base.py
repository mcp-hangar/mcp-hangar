"""Base provider launcher interface."""

from abc import ABC, abstractmethod

from ....stdio_client import StdioClient


class ProviderLauncher(ABC):
    """
    Abstract interface for launching providers.

    This is a domain service interface that defines how providers are started.
    Implementations handle the specific infrastructure details (subprocess, docker, etc.)
    """

    @abstractmethod
    def launch(self, *args, **kwargs) -> StdioClient:
        """
        Launch a provider and return a connected client.

        Returns:
            StdioClient connected to the launched provider

        Raises:
            ProviderStartError: If the provider fails to start
            ValidationError: If inputs fail security validation
        """
        pass
