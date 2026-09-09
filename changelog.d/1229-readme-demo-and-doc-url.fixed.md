**core:** the PyPI *Documentation* link 404'd. It pointed at
`https://mcp-hangar.io/getting-started/quickstart/`; the site serves that page
at `/docs/getting-started/quickstart`. The link is in the sidebar of the
package's own landing page, so it was the first thing a broken path cost.

The README's deny demo also referenced a `demo.yaml` it never created, and set
the tool description through a shell variable that cannot reach the upstream
when your MCP client is the one starting the gateway. It now writes the config
and carries the description in that config's `env:`.
