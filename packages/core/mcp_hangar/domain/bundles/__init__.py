"""Provider bundles - Curated collections of MCP providers.

Bundles provide sensible defaults for common use cases:
- Starter: Basic providers for general use
- Developer: Providers for software development workflows
- Data: Providers for data analysis and database access

Bundles are domain concepts that define which providers belong together.
"""

from .definitions import (
    Bundle,
    BUNDLES,
    DATA_BUNDLE,
    DEVELOPER_BUNDLE,
    get_all_bundles,
    get_bundle,
    get_provider_definition,
    ProviderDefinition,
    STARTER_BUNDLE,
)
from .resolver import BundleResolver, resolve_bundles

__all__ = [
    "Bundle",
    "ProviderDefinition",
    "BUNDLES",
    "STARTER_BUNDLE",
    "DEVELOPER_BUNDLE",
    "DATA_BUNDLE",
    "get_bundle",
    "get_all_bundles",
    "get_provider_definition",
    "BundleResolver",
    "resolve_bundles",
]
