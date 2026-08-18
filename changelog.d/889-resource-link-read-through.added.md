**core:** a `resource_link` the front door hands out is now resolvable on the
same gateway. Each relayed link is remembered per tenant (capability-style: a
reference handed to tenant A is unknown to tenant B), `resources/read`
forwards to the owning upstream, `resources/list` answers with the caller's
handed-out links, and `ui://` resources go through the fail-closed SEP-1865
guard (denied until an operator wires a policy). Before this the gateway
proxied the reference faithfully and then answered `Unknown resource` when
the client followed it. The full prompts/resources proxy (#889 -- upstream
catalogues, templates, subscriptions, completions) remains open
