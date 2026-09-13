**core:** a group's circuit breaker now opens after
`circuit_breaker.failure_threshold` failures in a row, as configured, not after
that many over the life of the process. A success, whether a call or a passing
health check, never reset the count while the circuit was closed. Only
`hangar_group_rebalance` did. So any long-lived group that saw an occasional
member failure eventually opened its circuit, even with every member healthy.
An open circuit still closes only once `min_healthy` members are back in
rotation
