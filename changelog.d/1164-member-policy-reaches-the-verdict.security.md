**core:** a group member's own tool/prompt/resource policy never reached the
verdict. `groups.<g>.members.<m>.tools` and the REST `member/<g>:<m>` scope
register under the member SERVER id, but every production caller -- the batch
gate, the post-approval revalidation and the front door -- identified the scope
with the caller's TENANT, so the lookup missed and the effective policy was
`_global -> group` alone: a documented member `deny_list` failed open, in both
topologies, on listing and on call. The resolver now takes the selected member
and the tenant as separate arguments and merges `_global -> mcp_server -> group
-> member server -> tenant`, which also brings in `mcp_servers.<m>.tools` and
`mcp_servers.<m>.tool_access.member.<tenant>` for a server reached through a
group
