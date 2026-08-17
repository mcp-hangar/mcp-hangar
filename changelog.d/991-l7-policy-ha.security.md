A compiled L7 egress policy now survives restarts and reaches every replica.
It was held only in the RAM of the replica that handled the operator's POST:
in HA the other replicas ran denied tools and a rolling restart dropped
enforcement everywhere, while the CR reported `Compiled`/`BackstopApplied`.
The policy is persisted on the fleet snapshot (the `enforce_ssrf` precedent),
restored with the row on startup and registration, and propagated live to
peers through the event tail. `GET /api/mcp_servers/{id}/l7_policy` (new,
`policy:read`) returns the attached policy or 404 -- previously the route had
no GET at all, so delivery could not be verified.
