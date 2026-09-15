**core:** a configuration reload no longer lets more calls run than the
concurrency limits allow. Every reload, a byte-identical one from the file
watcher included, replaced the concurrency limits. The calls already running
held their slots on the replaced limits, so up to twice the configured number
could run while they finished. A reload now changes a limit in place. A call in
flight keeps its slot, and its release frees that slot. A changed limit applies
to the calls that start after the reload. A reload that is refused leaves every
limit as it was: a server's `max_concurrency` used to be applied while the new
configuration was still being built. A negative `execution.max_concurrency`,
`execution.default_mcp_server_concurrency` or server `max_concurrency` is now
refused by the configuration check, before a reload stops any server. It used to
fail later, part-way through the reload.
