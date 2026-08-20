**core:** prompts and resources are governed. Both surfaces shipped ungoverned
within the tenant boundary; they now go through the *same* policy surface tools
use, re-keyed `(mcp_server, kind, name)` rather than grown a second time as
parallel `PromptAccessPolicy` / `ResourceAccessPolicy` objects. One resolver
chokepoint, so a listing and a fetch cannot drift apart, and prompts and
resources inherit the merge semantics, the approval gate, the per-tenant
overlays and the fail-closed front-door branch instead of a weaker copy of each.

New config, alongside the existing `tools:` block on an mcp_server or a group
(and inside a `tool_access.member.<tenant>` entry):

```yaml
access:
  prompt:   {deny_list: ["draft_*"]}
  resource: {allow_list: ["docs://*"]}
tool_projection:
  withdrawn_prompts: [retired_prompt]
  withdrawn_resources: ["demo://gone/1"]
```

`allow_list` / `deny_list` / `approval_list` mean exactly what they mean for
tools. Enforcement lands at both ends of every surface -- `prompts/list` +
`prompts/get`, `resources/list` + `resources/templates/list` + `resources/read`,
and the handed-out `resource_link` catalogue -- so a denied item is absent from
the listing AND refused on fetch, with the refusal indistinguishable from the
one a nonexistent item gets. A resource is matched by its **upstream** uri
(`demo://doc/1`), not the `hangar://<upstream>/…` projection of it: the upstream
form is the stable identity an operator writes, and the owning server is already
the policy scope.

Backward compatible by construction: every entry point defaults to
`kind: tool`, so a config written before this parses and decides identically and
governs tools only. The SEP-1865 `ui://` guard becomes a *case* of this surface
rather than a mechanism beside it -- it is the first gate on the resource path,
so an un-allowlisted `ui://` resource is now absent from the catalogue as well
as unreadable, and no resource policy, however permissive, can open it
