### `TracedMcpServerService` is removed; governance attributes now come from the call path

`mcp_hangar.application.services.TracedMcpServerService` and its legacy alias
`TracedProviderService` are gone, along with the
`application.services.traced_provider_service` module alias.

Nothing in Hangar constructed the class, so removing it changes no behaviour of
the gateway: the decorator wrapped a service the real invoke path never used,
which is why governance attributes did not appear on tool-call spans. ADR-029
retires it rather than reviving it, because wiring it in would have rebuilt a
second pipeline beside the one that actually runs.

**If you imported it,** you were tracing a path your calls did not take. The
attributes are now set by the gateway itself on `batch.call.<tool>`, so there
is nothing to construct and nothing to wire:

```python
# before -- traced nothing your callers reached
from mcp_hangar.application.services import TracedMcpServerService
service = TracedMcpServerService(mcp_server_service=inner, observability=adapter)

# after -- delete it; the attributes are on the span already
service = inner
```

`set_governance_attributes` in `mcp_hangar.observability.conventions` is
**kept**. An ADR-007 observability adapter may still call it with its own
process-local invocation data. Hangar's own boundary does not, because that
helper also asserts `gen_ai.operation.name=execute_tool` and
`mcp.method.name=tools/call`, which name the upstream call and stay on the
`execute_tool <tool>` CLIENT span.
