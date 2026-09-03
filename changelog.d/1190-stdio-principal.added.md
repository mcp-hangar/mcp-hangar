**core:** a stdio session can now be given an identity, so `tool_access.mode: front_door`
is reachable without a cluster. Declare the caller the spawning process implies:

```yaml
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [viewer]      # default; read-only
```

With the block present and the transport stdio, `tools/list` serves the upstream's own
flat tool names instead of the fail-closed empty list, per-tenant digest pins addressed to
that tenant are matchable (and no longer refused at boot), and the `hangar_*` management
surface follows the declared roles -- `viewer` shows the fleet reads and nothing that can
change state. No credential is checked, because a stdio server is not listening on
anything: the OS user who launched the process is the trust boundary (ADR-026).

Absent the block, nothing changes: the caller is anonymous and the front door stays empty.
HTTP ignores the block entirely and keeps using its credential channel.
