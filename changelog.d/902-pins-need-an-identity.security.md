**core:** digest pinning enforced nothing on a gateway with authentication
disabled, which is the configuration most evaluations run. A pin was
addressable only under `tool_projection.tenant_overrides.<tenant>.pins`, and
`resolve_pin` looked it up by tenant id -- but a tenant id reaches the call path
from exactly one place, `Principal.tenant_id`, and with auth off every caller is
anonymous and carries `None`. So no pin was ever matched, the gate took its "no
pin" branch, and every call went through unverified while `initialize` kept
advertising `io.mcp-hangar.digest-pinning` with all three enforcement modes.
Drift stayed computable and nothing stopped it. The same miss took out the task
path with it: the pin is what `create_task` binds a relayed task to, so tasks
were never bound to a digest either and the fail-closed re-verification on
result retrieval never had anything to check.

Pins can now be declared for all tenants, alongside the `withdrawn:` list they
mirror, and that block holds a caller carrying no tenant identity:

```yaml
tool_projection:
  digest_enforcement: block
  pins:
    refund: <sha256>
```

A pin declared for a specific tenant still wins over the all-tenants one for
that tenant -- narrowest first, the order the tool-access policies already
resolve in. And a configuration that declares per-tenant pins while
authentication is off no longer starts: it names the pins it found and the auth
setting that makes them unmatchable, and points at both ways out
