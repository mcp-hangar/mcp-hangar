**security:** a key an access policy does not read now refuses the boot and the
reload, naming its path, and `mcp-hangar config check` reports it. `tools: {allow:
[read], deny: [add]}` -- the shape the README showed -- parsed as a block with no
lists, which is no policy: the gateway booted, listed `add` and ran it. The
refusal covers every place a policy is written (a server's `tools`, a group's, a
group member's, `access.<kind>` and each `tool_access.member.<tenant>` entry),
suggests `allow_list` / `deny_list` / `approval_list` for the spellings people
reach for, and a refused reload keeps the previous policy. The README example is
corrected
