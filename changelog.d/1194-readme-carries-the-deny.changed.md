**core:** the README's Quickstart now shows what Hangar is for. It used to point at a
config, start a server, and stop -- which describes a proxy. It walks the same loop the
docs do: `init` (client found, tools pinned), the rug pull, the refusal
(`Tool 'echo' schema does not match its pinned digest`), and `pin --check` for CI. The
hand-written config example gained the two blocks that make it govern -- `tool_access` and
`auth.stdio.principal` -- because an example that omits them teaches a configuration that
enforces nothing.

**core:** the package description is now "Policy enforcement plane for MCP: every tool call
ends in a verdict. Self-hosted, MIT." It read "Production-grade infrastructure for Model
Context Protocol", which was retired from the site, the README and the registry entry
while `pyproject` kept feeding it to PyPI -- and from there to anything that reads PyPI
metadata. The keywords move with it: `security`, `policy-enforcement` and `tool-poisoning`
instead of `infrastructure`.
