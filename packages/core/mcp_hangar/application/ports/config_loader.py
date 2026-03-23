"""Config loader port.

Defines IConfigLoader so the reload handler can load and apply configuration
without importing from server.config (which is in the server layer).
"""

from abc import ABC, abstractmethod
from typing import Any


class IConfigLoader(ABC):
    """Interface for loading and applying provider configuration.

    Application layer uses this port; server.config provides the implementation.
    """

    @abstractmethod
    def load_from_file(self, path: str) -> dict[str, Any]:
        """Load and parse a configuration file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            Parsed configuration as a dictionary.

        Raises:
            ConfigurationError: If the file cannot be read or parsed.
        """

    @abstractmethod
    def apply_providers(self, providers_config: dict[str, Any]) -> None:
        """Apply a providers configuration section to the running system.

        Registers new providers, updates existing ones, etc.

        Args:
            providers_config: Mapping of provider_id -> provider spec dict.
        """
