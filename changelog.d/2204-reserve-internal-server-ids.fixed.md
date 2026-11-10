**core:** a server could be registered under `_global`, the id the tool access
resolver keeps the gateway-wide policy floor under, and would then share that
entry: a `hangar_load` with a tool list replaced the floor, and unloading the
server removed it. `_global` is now a reserved server id. `McpServerId` refuses
it, and the configuration file at startup, a reload and `hangar_load` refuse a
server, group or group member under it with an error that names the id.
