### a tool that drifted from its pin disappears from the listing

A pinned tool whose definition no longer matches its pin, under
`digest_enforcement: block` (the default), used to stay in `tools/list` and
`hangar_tools` and be refused when called with `Tool '<name>' schema does not
match its pinned digest`. It is now withheld: it is not listed, and on the
front door a call of it is answered `-32601 Tool '<name>' not found`, as for a
tool that does not exist. `hangar_call` still answers `ToolDigestMismatchError`.

What to do:

- Anything that waited for the digest-mismatch refusal on the front door to
  learn about drift should read `hangar_details <server>` (`withheld_tools`,
  reason `digest_drift`), the `withheld` field on `GET /api/tools` and
  `GET /api/mcp_servers/{id}/tools`, the `tool_digest_pin_drift_detected` log
  line, or the `DigestMismatchEvent`. Both are emitted once when the drift is
  first seen, whether or not the interval re-check is on.
- REST no longer returns a withheld tool's description or input schema, to any
  role: a withheld entry is its name, `digest`, `pinned_digest` and `withheld`
  only. A tool withheld from any tenant, or by a pin on a group that owns the
  server, is shown withheld there. Review the new definition at the upstream
  itself before re-pinning; `mcp-hangar pin --check` shows both digests.
- To bring the tool back after reviewing the change, re-pin it
  (`mcp-hangar pin --write`) and reload; clients are sent
  `notifications/tools/list_changed`.
- A server pinned under `warn` or `audit` is unchanged: its drifted tools stay
  listed and callable.
