**core:** new gauge `mcp_hangar_group_circuit_open{group}`. It reads 1 while
the replica that exposes it has the group's circuit breaker open, and 0
otherwise. Each replica keeps its own breaker, so two replicas can disagree
about one group. A client pinned to the replica whose circuit is open is then
refused, while the group serves from every other replica. The scrape's
`instance` label tells the replicas apart, and
`max by (group) (mcp_hangar_group_circuit_open) - min by (group) (mcp_hangar_group_circuit_open) > 0`
finds every group they disagree about.

The series exists from the moment a group is loaded. It changes on every
transition of the breaker, whether from a call, a health check or
`hangar_group_rebalance`, and it goes when the group is deleted. Its only
label is `group`, and it always agrees with `circuit_open` in
`hangar_group_list` on the same replica
