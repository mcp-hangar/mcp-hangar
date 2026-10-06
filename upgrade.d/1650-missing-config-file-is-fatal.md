### A missing configuration file stops the gateway

There is no built-in demo configuration any more. Before, when the
configuration file was not there, Hangar logged `config_not_found_using_default`
at INFO and booted a `math_subprocess` backend with every `hangar_*` tool --
stop, load and `reload_config` included -- and none of your pins, policies or
auth. That happened for a mistyped `--config`, an `MCP_CONFIG` naming a missing
file, a `config_path` given to `bootstrap()` or `Hangar.from_config()`, and a
start with no configuration file anywhere.

Now each of those refuses to start. `mcp-hangar serve`, a bare `mcp-hangar` and
`python -m mcp_hangar.server` exit 1 with the path and where it came from on
stderr, for example:

```text
Error: Configuration file /etc/hangar/confg.yaml (named on the command line) does not exist. Nothing is served without the configuration that was asked for.
```

`bootstrap()` and `Hangar.start()` raise `MissingConfigFileError` (a
`ConfigurationError`). A path that is a directory or is not readable is refused
the same way.

With no flag, no `MCP_CONFIG`, no `./config.yaml` and no
`~/.config/mcp-hangar/config.yaml`, the message says to run `mcp-hangar init`.

What to do: if a deployment logged `config_not_found_using_default`, it was
running the demo configuration. Fix the path, or create the file with
`mcp-hangar init`. For a gateway with no servers, use a file holding
`mcp_servers: {}`. From Python, a
configuration with no file goes in `bootstrap(config_dict=...)` or `Hangar.from_builder(...)`.
