**security:** a tool call's events are no longer lost when another writer
appends to the same server's stream at the same moment (GHSA-4wc3-66v8-968j).
Appending a batch at the end of a stream took two steps: read the stream's
version, then append at that version. A concurrent tool call, the health-check
or gc worker, or another replica could write in between. The append then
failed with a concurrency conflict. The batch was neither stored nor handed to
the metrics, audit, security and enforcement handlers. A tool call that had run
on the upstream was reported to the caller as failed.

Each event store now assigns the next version inside the write:

- the in-memory store, under its lock;
- SQLite, in one `BEGIN IMMEDIATE` transaction, which also holds across
  processes sharing the file;
- PostgreSQL, in a single `INSERT ... ON CONFLICT DO UPDATE` statement, which
  holds across replicas sharing the database.

A third-party `IEventStore` inherits a new `append_at_end` method that retries
a bounded number of times. If it still loses, the batch is handled like any
other store failure: it is delivered to handlers and logged as
`event_persistence_failed`, not dropped.

A caller that passes an explicit expected version still gets
`ConcurrencyError` on a real conflict. On SQLite, a conflict between two
processes is now reported that way too, instead of as a unique-constraint
error.

Publishing a command's events can still fail after the command ran. That no
longer changes the command's outcome. The failure is logged as
`event_publish_failed` and counted in
`mcp_hangar_errors_total{component="event_publish"}`, and the tool call's result
stands.

The event-sourced auth store (`auth.storage.driver: event_sourcing`) had the
same pattern:

- Assigning or revoking a role read the principal's version just before
  appending. A concurrent change to the same principal could fail the
  assignment or revocation with a concurrency conflict. Role changes are now
  appended at the end.
- An API-key revocation or rotation that loses a race to another change of the
  same key is now decided again on the key's current state, up to 16 times. A
  revocation lands. A rotation of a key that was revoked in the meantime is
  refused, and no new key is stored.
- A revocation no longer reports a key as missing while the key index is being
  built, or when the key was created through another replica sharing the event
  store.

In none of these cases was a change applied without its event being stored.
Each failure was reported to the caller.
