**security:** a digest pin the loader cannot read no longer leaves its tool
unpinned. Under `tool_projection.pins` and `tenant_overrides.<tenant>.pins`, a pin
written in uppercase or as `sha256:<hex>` was dropped although it named the right
digest, any other unreadable pin was dropped with a warning, and a `pins` block
or `tenant_overrides` entry that was not a mapping was skipped with no log line,
each time serving the tool unpinned while the gateway booted. The two spellings
now pin the tool. Any other unreadable pin is logged at ERROR and pins the tool
to a value no definition matches, so it is withheld under `block` until fixed.
A `pins` or `tenant_overrides` block that is not a mapping is refused at boot and
by `mcp-hangar config check`
