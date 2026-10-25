**core:** `from mcp_hangar.observability import *` no longer raises
`AttributeError`: its `__all__` listed an empty string left behind when a name was
removed. A test now checks that every name every package's `__all__` exports
exists
