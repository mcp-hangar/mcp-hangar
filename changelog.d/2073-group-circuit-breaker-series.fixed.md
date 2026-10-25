**observability:** deleting a group now also drops its
`mcp_hangar_circuit_breaker_state` series. The group's circuit events write that
gauge under the group's id, and only `mcp_hangar_group_circuit_open` was removed,
so a group deleted with its circuit open read `state="open"` 1 forever
