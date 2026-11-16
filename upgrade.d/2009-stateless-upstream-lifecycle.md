### an HTTP upstream on 2026-07-28 is spoken to statelessly

Starting a `remote` server now sends `server/discover` before anything else. An upstream that answers with 2026-07-28 in `supportedVersions` gets no `initialize` and no `notifications/initialized`, and holds no `Mcp-Session-Id`. Its upstream log shows one `server/discover` per start in their place. Any other answer, including a method-not-found or an HTTP 400, falls back to the `initialize` handshake exactly as before, so an upstream on 2025-11-25 or earlier sees one extra refused `server/discover` and nothing else.

An SDK 2.x upstream serves both eras, so it now runs stateless. Hangar opens no standing `GET` stream to a stateless upstream, because the 2026-07-28 revision has none. Until Hangar listens with `subscriptions/listen`, it receives no unprompted message from such an upstream: `notifications/tools/list_changed`, `notifications/resources/updated`, the other `*/list_changed` notifications it relays to front-door subscribers, and progress sent outside a response. A changed catalogue is picked up when the server restarts.

To keep those notifications, declare the upstream `legacy`. Hangar then never sends it `server/discover` and always runs the `initialize` handshake, including when it renegotiates a lost session:

```yaml
mcp_servers:
  search:
    mode: remote
    endpoint: https://search.internal/mcp
    upstream_protocol: legacy   # auto (the default) probes server/discover
```

The key takes `auto` or `legacy`. Any other value, and the key on a `subprocess`, `docker` or `container` server, is refused at load.
