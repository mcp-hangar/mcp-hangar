**core:** the gateway never finished the MCP handshake with an upstream. It
sent `initialize` and then went straight to `tools/list`, skipping the
`notifications/initialized` the lifecycle requires, so every upstream -- stdio,
docker and remote alike -- was left permanently mid-handshake.

A server is entitled to defer work until that notification arrives, and the
official reference server does exactly that: a tool registered in its
`oninitialized` handler was neither listed nor callable through Hangar (12
tools discovered where a finished session sees 13), with nothing logged to
suggest anything was missing. If your upstream registers tools, prompts or
resources on initialization, this release discovers them for the first time --
so a catalogue may legitimately grow after upgrading.

The notification is best-effort: an upstream that mishandles it gets a warning
in the log, not a failed start. Both transports gained a `notify()` primitive
to make it possible at all -- neither could previously send a message without
an id.
