### `headers.param_validation.required` now refuses `hangar_call` with `Mcp-Param-*` headers

With `headers.param_validation.required: true`, a `hangar_call` request that
carries any `Mcp-Param-*` header is now refused with `HEADER_MISMATCH` (-32020)
and the message "the request's Mcp-Param-* headers could not be validated
against its body". Before, it was served. `hangar_call` declares no
`x-mcp-header`, so none of its `Mcp-Param-*` headers is ever checked against the
body, and none reaches an L7 selector either.

The same refusal now applies on the front door to a call carrying an
`Mcp-Param-*` header the called tool does not declare, next to a declared one,
and to a handshake-era call carrying an `Mcp-Param-*` header. Before, only a
failed pre-dispatch listing was refused.

A request with no `Mcp-Param-*` header is not affected, and neither is any
deployment that leaves `required` at its default, `false`. To keep such calls
working under `required`, stop sending `Mcp-Param-*` headers on `hangar_call`, or
call the tool on the front door with the tool declaring the header through
`x-mcp-header`.
