### `max_consecutive_failures` must be 1 to 100

A server whose `max_consecutive_failures` is 0, above 100, or not an integer now refuses the config instead of booting. Set it inside 1-100 (the default is 3) before upgrading; `mcp-hangar config check` does not flag it, so look for it with `grep -rn max_consecutive_failures`.
