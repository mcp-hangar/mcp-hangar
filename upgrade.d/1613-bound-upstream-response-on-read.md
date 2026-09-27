### An upstream response over the size limit fails the call instead of coming back dropped

Until this release a `hangar_call` result over 10 MB (10 485 760 bytes) was
read whole, then dropped: its entry came back with `success: true`,
`result: null`, `truncated: true`, `truncated_reason: "response_size_exceeded"`
and `original_size_bytes`, the trace carried a `hangar.shaping.drop` event and
`mcp_hangar_batch_truncations_total{reason="per_call"}` went up. A flat call and
the facade's `invoke` were not cut.

The limit is now enforced while the upstream response is read, over stdio and
HTTP, for every surface, and its default is 32 MiB (33 554 432 bytes). A
response over it is not read to the end, and the call fails with
`error_type: ResponseTooLarge` and the message `The upstream response exceeded
the limit of <n> bytes and was not read.`, the same on `hangar_call`, on a flat
call (a tool error, `isError: true`) and on the facade. The call span ends in
ERROR with `error.type=ResponseTooLarge`. Nothing that was served before is
refused at the default, since every response under 10 MB is under 32 MiB.

- A client or alert that looked for `truncated_reason: "response_size_exceeded"`
  should look for `error_type: "ResponseTooLarge"` instead. The
  `reason="per_call"` series of `mcp_hangar_batch_truncations_total` is no
  longer written; `reason="batch_budget"` is unchanged, and so is batch
  truncation, with its continuation.
- To change the limit, set `execution.max_response_bytes` in the config file,
  or `MCP_MAX_RESPONSE_BYTES` in the environment, which wins over the file. A
  server can set its own `max_response_bytes` in its `mcp_servers` entry, which
  wins over both. Each must be a positive whole number of bytes; any other value
  refuses the config.
