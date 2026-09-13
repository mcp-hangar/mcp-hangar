**core:** `hangar_status` answered with one replica's view in a shape that read
as the fleet's. Under session affinity two calls can reach two replicas, so an
operator saw the fleet "recover" or "collapse" between calls when they had
only reached a different pod. `hangar_status` and `hangar_health` now say which
replica answered. Both carry `replica` (`instance_id`, `uptime_seconds`,
`uptime`), `scope: "replica"` and a `scope_note`. `instance_id` is the same
identity the management lease, the peer event tailer and `GET /system` use. The
rendered dashboard opens with `Answered by replica: <id>` and labels its uptime
`Replica uptime`. The two tools also read one snapshot now, so from the same
replica they agree. `hangar_health` previously left hot-loaded servers out of
`mcp_servers.total` and `by_state`, and it now counts them, as `hangar_status`
always did. Existing fields are unchanged; `summary.uptime` and
`summary.uptime_seconds` remain as the answering replica's uptime. For a
fleet-wide view, query the per-replica metrics in Prometheus.
