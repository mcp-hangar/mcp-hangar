### A `pins` or `tenant_overrides` block that is not a mapping is refused

`tool_projection.pins`, `tool_projection.tenant_overrides` and each
`tenant_overrides.<tenant>` (with its `pins`) must be mappings. A list or a
string there used to be skipped without a word, which left every tool it was
meant to pin unpinned; it now refuses the configuration at boot, on reload and
in `mcp-hangar config check`, naming the block. An empty key (`pins:` with
nothing under it) is still nothing.

A single pin value that is not a 64-character hex digest no longer disappears
either. Uppercase hex and the `sha256:<hex>` spelling are read as the digest they
name. Anything else is logged at ERROR as `invalid_config_digest_pin` and holds
its tool as drifted, so under `digest_enforcement: block` that tool is withheld
until the pin is corrected, for example with `mcp-hangar pin --write`. Run
`mcp-hangar config check` and look for `invalid_config_digest_pin` in the boot
log before upgrading if your pins are hand-written.

Three consequences to check if your pins are hand-written:

- A pin written in uppercase or as `sha256:<hex>` used to be ignored; it now
  enforces. If it was stale -- the tool changed since it was taken -- that tool
  is now withheld under `block` (the default). `mcp-hangar pin --check` shows it.
- Under `new_tools: withhold`, an unreadable pin does not admit its tool: the
  tool stays withheld, whatever `digest_enforcement` says. And a server whose
  only pins are unreadable now has a baseline, so every other tool it serves is
  withheld too, where before there was no baseline and everything was served.
- Under `digest_enforcement: audit` or `warn`, an unreadable pin reports drift on
  every recheck until it is fixed.
