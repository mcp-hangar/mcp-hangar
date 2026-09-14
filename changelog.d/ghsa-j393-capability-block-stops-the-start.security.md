**security:** `enforcement_mode: block` and `enforcement_mode: quarantine` now
stop a server that serves a tool outside its declared
`capabilities.tools.expected_tools` (GHSA-j393-8j4v-v73r). Before, block mode
found the drift only after the start had completed. The server was marked
`dead`, but the call that started it still invoked the tool it asked for,
undeclared tools included, and the upstream process was left running. A tool
that appeared after the start was not checked at all. Quarantine, documented to
stop a server serving new requests, did not act on the drift either: it served
the server as `alert` does.

In both modes the start that finds the drift now fails with
`CapabilityBlockedError`. Its connection is closed, the server goes to `dead`
for a capability block without a `McpServerStarted` event, and the
`CapabilityViolationDetected` event is kept. Quarantine also records
`McpServerCapabilityQuarantined`. No call starts the server again: every later
call to any of its tools is refused, and a group does not put it in rotation. A
tool that appears after the start, through a refresh or `tools/list_changed`,
blocks the server the same way at its next call. Prompts, resources and task
relays to the server are refused as well. A deliberate start, such as
`hangar_start`, checks the tools again. `alert` mode is unchanged.
