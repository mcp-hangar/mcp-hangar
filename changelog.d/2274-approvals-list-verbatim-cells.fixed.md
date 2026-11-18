**cli:** `mcp-hangar approvals list` showed a held call differently from what it
asked. Every table cell was parsed as Rich markup, so arguments such as
`[rm -rf /]` were listed as nothing and `[conceal]` hid text on the terminal,
and non-ASCII arguments were escaped (`Treść`). Cells are now listed
verbatim, non-ASCII as written, with control, format and bidi characters
escaped. The `--json` output is unchanged
