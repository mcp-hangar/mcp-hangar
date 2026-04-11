"""Runtime provider store contract.

Defines IRuntimeProviderStore so application layer can look up hot-loaded
(runtime) providers without depending on infrastructure.RuntimeProviderStore.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider_runtime import ProviderRuntime


class IRuntimeProviderStore(ABC):
    """Read-only interface for looking up runtime (hot-loaded) providers.

    Application command and query handlers use this to check runtime providers
    in addition to the static repository.
    """

    @abstractmethod
    def get_provider(self, provider_id: str) -> "ProviderRuntime | None":
        """Return the runtime provider with the given ID, or None if not found.

        Args:
            provider_id: The provider ID to look up.

        Returns:
            ProviderRuntime instance, or None if not found.
        """
