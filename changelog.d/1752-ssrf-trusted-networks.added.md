**core:** `ssrf.trusted_networks` in `config.yaml` lists the private networks
that a remote endpoint registered through `POST /api/mcp_servers` may resolve
into, such as a Kubernetes cluster's Service range for the operator. The
registration check and the per-request connect-time check both read the list.
A reload that narrows the list refuses the next connection. Loopback,
link-local and the unspecified ranges can never be trusted. The list applies to
every caller with `mcp_servers:write`.
