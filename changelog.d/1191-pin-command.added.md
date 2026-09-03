**core:** `mcp-hangar pin` computes the tool digests Hangar enforces, so digest pinning can
be adopted without deriving a SHA-256 by hand:

```bash
mcp-hangar pin                 # print {tool: sha256} per server
mcp-hangar pin --write         # merge them into tool_projection.pins
mcp-hangar pin --check         # exit 1 when the file and the servers disagree
```

Digests come from `compute_tool_digest`, the same function the projection registry uses to
build what the gate compares against, so a written pin matches by construction. `--check`
is the pre-commit/CI form: it prints the pinned and the served digest per drifted tool and
exits non-zero (`--json` for scripts). Exit codes: 0 agreement, 1 drift, 2 the question
could not be answered -- missing config, unknown `--server`, or a server that never
started.

`--write` rewrites the configuration file through PyYAML, which round-trips values but not
comments or key order, and keeps the previous file beside it as `<config>.bak`. Pins cover
the tool description as well as its schema, so a poisoned description with unchanged
parameters is drift.

The smoke test's timeout budget moved with this: it was 10s for the whole fleet and halved
again per server, which is less than a cold `uvx` download takes, so `init` reported
`reader_died` for servers that were merely slow to arrive. It is now 60s overall with a
30s floor per server.
