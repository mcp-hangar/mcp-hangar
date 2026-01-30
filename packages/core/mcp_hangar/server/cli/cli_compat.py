"""Backward compatibility layer for the original CLIConfig.

This module re-exports the original CLIConfig from the old cli.py module
to maintain backward compatibility with existing server code that expects
the old dataclass structure.
"""

# Re-export from original location for backward compatibility
from ..cli_legacy import CLIConfig, parse_args

__all__ = ["CLIConfig", "parse_args"]
