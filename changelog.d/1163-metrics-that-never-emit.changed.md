**core:** `mcp_hangar_build` and `mcp_hangar_process_start_time_seconds` are
now set at startup instead of being exposed as a TYPE header with no sample,
and `mcp_hangar_discovery_conflicts` -- which nothing incremented, no dashboard
drew and no document mentioned -- is removed. A test now asserts that every
registered metric has something that writes to it, next to the one that asserts
every metric is registered
