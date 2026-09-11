**core:** failures Hangar catches and carries on from now end their span as
ERROR, with `error.type` set to the exception's class name. Six fault barriers
recorded the exception but left the span's status UNSET, so a trace showed the
failed operation as a success: an event handler or hook subscriber that raised
(`event.publish.<Event>`, `event.publish_hook.<phase>`), a failed event store
write (`event_store.append`), a failed discovery cycle (`discovery.cycle`), a
discovered server whose registration raised (`discovery.process_mcp_server`),
and a failed cold start in a batch call (`mcp_server.cold_start`). On a span
several handlers share, the first failure sets `error.type` and a later
success does not clear it. Nothing else changes: events are still delivered
when the store write fails, discovery still counts and skips as before,
`discovery.result` and `cold_start.result` keep their values, and the batch
call still returns the same cold-start refusal. A refusal is not an error, so
a control plane that rejects a discovered server still leaves its span UNSET
