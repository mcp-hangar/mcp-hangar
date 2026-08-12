The built-in approval delivery channel is now called `event_stream`, because
that is where an approval notification actually travels. It was called
`dashboard`, after a management UI that shipped with the Hangar Cloud tier and
was archived with it — a channel named after a product that no longer exists,
whose `send()` wrote a log line and pushed to nothing, while the docstring
claimed a WebSocket integration "wired via event bus" that was never wired.

The push is real, just upstream of delivery: the gate publishes
`ToolApprovalRequested` before it waits, and `/api/ws/events` streams every
domain event, so any client holding `audit:read` sees a held call — id, tool,
channel label, expiry — in real time. The channel is now named after that
surface.

`channel: dashboard` still resolves, to the same delivery, and logs
`approval_delivery_channel_renamed` once at boot saying where the name went; its
config block is still read. Nothing to change on upgrade. `approval_channel`
defaults to `event_stream` on new policies; existing approval records keep
whatever label they were written with.

Also removed: `hangar_approve_prompt`, a tool nothing registered, whose docstring
pointed at an `approvals.channel: mcp_prompt` that no builtin or entry point has
provided since 2.0.
