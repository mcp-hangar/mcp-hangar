### REST tool listings mark a withdrawn or unadmitted tool `withheld: "withdrawn"` and drop its description

`GET /api/mcp_servers/{id}/tools`, `GET /api/tools/` and `GET /api/mcp_servers/{id}` show a tool a call of it is refused as withdrawn the way they already show a tool that drifted from a blocking pin: its name, `digest`, `pinned_digest` when it has one, and `withheld`, with no `description` and no `inputSchema` (`input_schema` on `/api/tools/`). The new `withheld` value is `"withdrawn"`, beside the existing `"digest_drift"`. It applies to:

- a tool `tool_projection.withdrawn:` (or a runtime withdrawal) covers for every tenant, on the server or a group that owns it;
- under `tool_projection.new_tools: withhold`, a tool the server's pins do not admit for every tenant. A tool pinned only for some tenants is shown withheld here too, because these routes answer for no tenant.

A client that reads `description` or `inputSchema` off every entry should read `withheld` first, as it should for `"digest_drift"`. A withdrawal for some tenants only is not marked.

`new_tools: withhold` declared on a group's `tool_projection`, beside pins declared on the group, now withholds: the access decision `hangar_call` and the listings make refuses a tool the group's pins do not admit as withdrawn, whether the call names the group or one of its members -- including a direct call to a member whose own `tool_projection` sets no `new_tools`. Before, it withheld nothing, because a group's tools are catalogued under its members' ids, and under the group id every tool read as unknown.

Two more things change for a server under `new_tools: withhold` with pins:

- A call naming a tool the pins do not cover is refused as withdrawn
  (`ToolWithdrawnError`), on a warm server as on a cold one -- including a
  misspelled tool name, which used to answer not-found.
- On a cold server, such a call is refused before the server is started, so it
  no longer starts the server and no `ToolAdmissionRequested` is published for
  it; the admission request appears once something else starts the server.
