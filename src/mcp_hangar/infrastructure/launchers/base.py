"""Base provider launcher interface."""

from __future__ import annotations


class ProviderLauncher:
    """Infrastructure base class for provider launchers."""

    def stop(self, provider_id: str) -> None:
        """Stop a provider previously launched by this launcher."""
        _ = provider_id
