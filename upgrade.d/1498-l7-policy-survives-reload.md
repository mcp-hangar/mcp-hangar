### an L7 egress policy set over the API survives a reload

An L7 egress policy is set at runtime, through `POST
/api/mcp_servers/{id}/l7_policy` or the fleet projection, and no configuration
file declares one. A reload that rebuilt a server -- because a setting it is
built from, such as `env`, changed -- dropped that policy, and reported
success.

A rebuild now carries the running server's policy onto its replacement, and
logs `l7_policy_carried_to_rebuilt_mcp_server` when it does. A deployment that
relied on a reload clearing a policy must now clear it explicitly, with `DELETE
/api/mcp_servers/{id}/l7_policy`.
