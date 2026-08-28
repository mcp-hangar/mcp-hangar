**core:** `examples/quickstart` runs a provider that exists. Its `config.yaml`
declared `image: my-org/openai-mcp:latest` running `python -m
openai_mcp_server` -- an image and a module that have never existed -- so the
example the README points at could not work, and `examples/**` had no CI that
would notice. It now runs the official filesystem server from
`modelcontextprotocol/servers`, as the image Docker publishes for it, with the
capability block rewritten to describe that provider rather than the fictional
one.

Because a container-mode provider is started through the Docker API, the
compose file now mounts the Docker socket and adds the gateway to its group.
**Access to that socket is equivalent to root on the host** -- it is there
because this example runs a container provider, and a subprocess or remote
provider needs none of it.

The `examples-compose` CI job now asks the gateway for `tools/list` and fails
on an empty projection. Healthy-and-config-loaded was true of the broken
version too.
