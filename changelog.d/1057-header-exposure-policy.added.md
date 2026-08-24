**core:** a new per-server (or per-group) `header_exposure:` block governs which
parameters an upstream may oblige a client to send as an HTTP header. SEP-2243
lets a tool annotate an `inputSchema` property with `x-mcp-header`, and its only
defence against annotating a secret is a SHOULD NOT -- so an upstream that
annotates `api_key` puts the key in front of every intermediary on the path.
`deny_annotated` globs are matched against both the annotation token and the
property path; `on_violation` is `warn` (the default, which changes nobody's
surface), `withdraw`, or `refuse_boot`. An unknown `on_violation` is refused at
parse rather than resolving to the default. The schema is never edited, so
digests and pins do not move.
