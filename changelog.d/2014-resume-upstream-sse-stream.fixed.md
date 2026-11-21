**core:** a call to a remote upstream that closed its SSE response stream
before it answered failed with "SSE response did not contain valid JSON-RPC
response", although streamable HTTP lets an upstream do that (SEP-1699) and
deliver the answer on a resumed stream. Hangar, as the client of its upstreams,
now waits the upstream's `retry` interval (one second without one), opens a GET
that names the last event id in `Last-Event-ID`, and takes the answer there,
re-opening from the newer id if that stream closes too, all inside the call's
timeout (mcp-hangar#2014). The standing GET stream also waits `retry` and sends
`Last-Event-ID` when it re-opens, where it re-opened at once before. A
2026-07-28 connection has no GET stream and does not resume, and Hangar's own
server streams still carry no event id or `retry` (mcp-hangar#2232).
