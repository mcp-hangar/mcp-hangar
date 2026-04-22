"""Config loader port.

Defines IConfigLoader so the reload handler can load and apply configuration
without importing from server.config (which is in the server layer).
"""

from abc import ABC, abstractmethod
from typing import Any


class IConfigLoader(ABC):
    """Interface for loading and applying mcp_server configuration.

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
    def apply_mcp_servers(self, mcp_servers_config: dict[str, Any]) -> None:
        """Apply a mcp_servers configuration section to the running system.

        Registers new mcp_servers, updates existing ones, etc.

        Args:
            mcp_servers_config: Mapping of mcp_server_id -> mcp_server spec dict.
        """
