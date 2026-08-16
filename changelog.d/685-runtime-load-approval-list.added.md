**core:** `hangar_load` accepts `approval_tools`, so a server registered at
runtime can put a tool behind human approval — the third outcome the YAML
`tools:` surface already had. A load that asks for approval on a deployment
with no approval gate is refused rather than registering a policy nothing
enforces.
