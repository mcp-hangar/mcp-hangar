**core:** `examples/quickstart` runs a provider that exists. Its `config.yaml`
declared `image: my-org/openai-mcp:latest` running `python -m
openai_mcp_server` -- an image and a module that have never existed -- so the
example the README points at could not work, and `examples/**` had no CI that
would notice. It now runs the official **everything** server from
`modelcontextprotocol/servers` as a second compose service, reached with
`mode: remote`, with the capability block rewritten to describe that provider
rather than the fictional one.

Two constraints decided that shape, and both are worth knowing before writing
a config of your own. `mode: container` cannot work from inside the published
Hangar image: container mode shells out to a `podman` or `docker` CLI on the
host running Hangar, and the image has neither -- mounting the Docker socket
does not help, because the socket is not what it uses. And `everything` is the
only official server that speaks HTTP; the rest are stdio-only, which a gateway
in its own container cannot attach to without a bridge beside it.

The `examples-compose` CI job now **calls** the provider through
`hangar_call` and fails if it does not answer -- config, Docker socket,
container start and tool invocation, end to end. Healthy-and-config-loaded was
true of the broken version too, and so was `tools/list`: in the default
topology that returns the gateway's own `hangar_*` API whether or not a single
provider works.
