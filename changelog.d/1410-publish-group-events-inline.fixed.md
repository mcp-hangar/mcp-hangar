**core:** a group's events now go out on the path that raised them. A member
leaving or rejoining rotation, the circuit opening or closing, and a rebalance
were recorded on the aggregate and drained only by the group CRUD handlers, so
they reached subscribers late, in a batch with whatever else had piled up, or
-- if nobody edited the group -- never, and a restart lost them. The executor
drains the group after it reports a call's outcome, the rebalance saga after it
reports a health check, and both rebalance surfaces after they rebalance.

These events are delivered to this replica's subscribers and deliberately not
appended to the shared event log. A group's rotation and circuit breaker live
in each replica's own memory, so "the circuit opened" is a fact about one pod
and not about the group; sharing that state across replicas is the open,
ADR-gated decision in #1358, and writing breaker transitions to the log is the
mechanism it defers. A group's configuration -- created, updated, member added
or removed, deleted -- is unchanged and still goes to the group's stream.
