**core:** `max_retries`, `retry_backoff_factor` and `retry_status_codes` on an
HTTP upstream now do what they say. The only retry that ran was httpcore's,
which retries connect failures alone on its own hardcoded backoff, so a
502/503/504 from an upstream mid-rollout came back to the caller on the first
attempt and the other two settings had no reader at all. The client retries the
configured statuses and connect failures itself, under the configured backoff,
and emits `mcp_hangar_http_retries_total` -- registered, on a shipped Grafana
panel and in the docs, and never incremented until now
