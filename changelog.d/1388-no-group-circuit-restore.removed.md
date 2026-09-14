**core:** a gateway no longer saves its groups' circuit breakers to the saga
state store on shutdown, and bootstrap no longer tries to read them back. The
read never restored anything. It ran before the groups were loaded, so every
group has always started with a closed circuit, and it looked up one row for all
groups rather than each group's own. A group's circuit now closes by itself once
its members recover (#1383), so a circuit saved as open hours earlier would
only have held traffic off a group that is healthy again. A group's circuit
breaker stays local to one replica and one process.

A `circuit_breaker` row an older version wrote stays in `saga_state`, and
nothing reads or rewrites it. It is safe to leave it or delete it. The log
events `circuit_breaker_saved`, `circuit_breaker_save_skipped` and
`circuit_breaker_save_failed` are gone; `circuit_breaker_restored` never fired.
