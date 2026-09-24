**core:** `/metrics` now says which L7 egress policies a gateway replica holds,
and when each last arrived. `mcp_hangar_l7_policy_held{mcp_server, mode}` is 1
for every server holding a policy (`mode` is `Enforce` or `Audit`) and absent
for one holding none; it is read from the servers at scrape time, so a policy
installed by the operator push, the peer event tail, startup recovery or a
reload shows up the same way. `mcp_hangar_l7_policy_last_set_timestamp_seconds{mcp_server}`
is the Unix time this replica last accepted a set or a clear for the server. A
replica that restarted without a durable backend and has not been re-delivered
its policy shows no `held` series, so comparing the MCPEgressPolicy CRs against
this family finds the gap #1306 left silent
