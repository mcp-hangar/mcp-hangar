**core:** a restart no longer drops the L7 egress policy the operator pushed to
a server that `config.yaml` declares. The push over `POST
/api/mcp_servers/{id}/l7_policy` stored the policy on the server's fleet row,
but startup recovery skipped the row of every server the file already declared,
so the restarted gateway served calls the policy denies until the operator's
next reconcile, while the `MCPEgressPolicy` still reported `Enforce`.

Recovery still lets the file win for every setting the file declares, and now
carries the stored policy onto the server the file built, the same rule a
reload applies since #1498. A stored policy that no longer parses is counted as
a failed recovery and logged at error instead of being skipped.

The push response now carries `persisted`: whether a restart of this gateway
gives the policy back. It is `false` with no persistence backend -- the chart
default -- or with `MCP_AUTO_RECOVER=false`, and each such push logs
`l7_policy_not_persisted` at warning. See the upgrade note for which
deployments keep the policy.
