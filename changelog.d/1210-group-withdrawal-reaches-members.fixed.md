**core:** a `tool_projection.withdrawn` (or `withdrawn_resources`/`withdrawn_prompts`)
declared on a **group** withdrew nothing. It was registered under the group id,
but `tools/list` and a tool call always ask under the group's MEMBER id, and
that direction of the group/member collapse was missing -- so the withdrawal
was silently never consulted. The symptom impersonated success: a name
collision between a group member and an unrelated server (#857 drops both
sides) looked exactly like the withdrawal having worked, on the wrong server.
`_withdrawal_scopes` now resolves a member id to its owning group too,
symmetric with how the access-policy half already worked.
