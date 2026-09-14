**security:** a truncated result's continuation now answers only the caller
whose `hangar_call` produced it (GHSA-pfrp-gjcw-mxq3). With response truncation
on, the rest of a truncated result was cached under its continuation id alone.
`hangar_fetch_continuation` returned it and `hangar_delete_continuation` deleted
it for any caller that presented the id, and the INFO `result_truncated` log
line printed the id in full.

- The cache records the tenant and principal of the caller with the payload, in
  both the memory and the Redis backend. A fetch or delete from any other caller
  gets the same answer as an id that does not exist. That includes another
  principal in the same tenant, and a caller with no tenant.
- A tenant-scoped `tool:invoke` grant, which the continuation tools accept, now
  reaches only that caller's own continuations.
- No log line carries a continuation id's random suffix. `result_truncated`
  logs `batch_id` and `call_index`, and the cache and continuation tools log
  `continuation_ref`, the id without its suffix. A fetch or delete of another
  caller's continuation logs a `continuation_owner_mismatch` warning.
- With auth off, the caller that makes the call and the caller that fetches
  both have no identity, and continuations work as before.
