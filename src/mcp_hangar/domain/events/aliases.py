"""Legacy module-level aliases for renamed event classes.

Assignments rather than subclasses: these four were pure renames with no
behaviour of their own, so an alias is the whole story. The ``Provider*``
classes that DO subclass live beside their bases in the other modules.

Remove together with the deprecated ``provider_id`` keyword (planned 2026-Q3).
"""

from .operations import (
    McpServerHotLoaded,
    McpServerHotUnloaded,
    McpServerLoadAttempted,
    McpServerLoadFailed,
)

ProviderLoadAttempted = McpServerLoadAttempted
ProviderHotLoaded = McpServerHotLoaded
ProviderLoadFailed = McpServerLoadFailed
ProviderHotUnloaded = McpServerHotUnloaded
