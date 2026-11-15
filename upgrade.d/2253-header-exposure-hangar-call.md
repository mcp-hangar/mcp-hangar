### `header_exposure` refuses a withheld tool on `hangar_call` too

A `header_exposure` block with `on_violation: withdraw` or `refuse_boot` used to
withhold a tool only from the flat listing. `hangar_call` still invoked it. The
same goes for a tool with an invalid `x-mcp-header` annotation. Such a call is
now refused with `ToolHeaderWithheldError`. If a client calls a tool through
`hangar_call` that the block withholds, change the block or stop calling the
tool. A `warn` block changes nothing.

The verdict needs the tool's schema from the catalogue. Under a block that
withholds, a tool whose schema is still unknown after its cold start is refused
as `schema_unknown`. A group member that starts while the configuration loads
(`auto_start`, the default) is not in the catalogue today, so while a
withholding block governs it, every tool on it is refused through `hangar_call`.
Its tools were already missing from the flat listing for the same reason.
