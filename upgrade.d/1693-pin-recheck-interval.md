### A pinned server's catalogue is re-listed every 60 seconds

Before, the catalogue a digest pin is checked against was refreshed only when a
server started and when its upstream sent `tools/list_changed`. An upstream
that changed a pinned tool's schema without announcing it was served under the
old pin until the gateway restarted.

Now every READY server that a pin covers -- a `tool_projection.pins` or
`tenant_overrides.<tenant>.pins` entry on the server itself or on a group it is
a member of -- is re-listed on an interval, set by a new top-level key:

```yaml
tool_projection:
  pin_recheck_interval_s: 60   # the default; 0 turns the re-check off
```

A non-zero value must be between 5 and 3600; anything else, a string or a
boolean included, refuses the boot. The value is read at start, so a reload
does not change it. Servers that are `cold`, starting, degraded or DEAD are
not listed and not started.

What to expect:

- A drifted pinned tool is refused (`ToolDigestMismatchError`, gate reason
  `digest_mismatch`) at most one interval, plus the time the pass takes, after
  the upstream changed it. The `tool_digest_pin_drift_detected` warning and one
  `DigestMismatchEvent` are emitted when the pass first sees it.
- One `tools/list` request per pinned server per interval reaches its
  upstream, from every replica.
- A re-list also refreshes the rest of the catalogue. A tool the upstream added
  without announcing it is now seen within one interval as well. On a pinned
  server whose `capabilities` enforcement blocks or quarantines a tool outside
  `expected_tools`, that server is now blocked at its next call instead of
  running on until a restart.
