### An access policy with a key nothing reads refuses the configuration

A policy block may carry only `allow_list`, `deny_list`, `approval_list`,
`approval_timeout_seconds` and `approval_channel` (and `access` in a
`tool_access.member.<tenant>` entry). Any other key now refuses startup and a
reload, naming the path and the key. Before, such a block was read as no policy
at all, so the tools it meant to restrict were served.

- **If you wrote `tools: {allow: [...], deny: [...]}`**, as the README did: you
  have had no restriction. Rename the keys to `allow_list` and `deny_list`, and
  check what the gateway served in the meantime.
- Run `mcp-hangar config check <file>` before upgrading: it reports the same
  keys and exits 1.
- A reload that hits this keeps the policy that was in force.
