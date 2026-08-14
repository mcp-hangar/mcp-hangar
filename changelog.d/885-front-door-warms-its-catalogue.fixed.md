A `front_door` gateway now starts every configured mcp_server when it starts, so
`tools/list` stops being a readout of one replica's warm-up history. Previously a
replica that had started nothing had discovered nothing, so after any restart it
served an empty tool list to a valid tenant with no client-reachable way to fix it
(the meta-API is not projected for an ordinary tenant, a known tool name resolves
against the same empty map, and health checks skip cold servers), and two replicas
that had warmed different servers answered the same tenant differently. Warming runs
on its own thread so readiness never waits on a backend handshake, and a backend that
fails to start is logged (`front_door_warmup_failed`) rather than costing the others
their projection. `egress` mode is unchanged: backends still start lazily on first
use. (#878, #885, #886)
