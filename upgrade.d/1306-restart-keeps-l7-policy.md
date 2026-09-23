### a pushed L7 policy survives a restart only when the gateway keeps its fleet

The operator delivers an `MCPEgressPolicy` to the gateway as a compiled L7
policy, over `POST /api/mcp_servers/{id}/l7_policy`, and re-delivers it only on
its next reconcile. Whatever the gateway does not keep across a restart is
therefore not enforced between the restart and that reconcile -- 2h16m in the
report behind #1306 -- while the CR still reads `Compiled`. The network backstop
is not affected: it lives in the cluster, not in the gateway.

Until this release, a restart dropped the policy of every server `config.yaml`
declares, whatever the backend. Now whether a restart keeps it depends only on
how the gateway stores its fleet:

| Deployment | After a gateway restart | `persisted` in the push response |
| ---------- | ----------------------- | -------------------------------- |
| No persistence backend (the chart default, `persistence.backend: ""`) | lost until the operator re-delivers | `false`, and `l7_policy_not_persisted` is logged at warning |
| `sqlite` on the chart's default emptyDir | kept across a container restart, lost when the pod is replaced (every rollout) | `true` -- the gateway cannot see what its volume is |
| `sqlite` with `persistence.sqlite.persistentVolume.enabled: true` | kept | `true` |
| `postgresql` | kept; each replica reads it back at start | `true` |
| `MCP_PERSISTENCE_ENABLED=true` with `MCP_AUTO_RECOVER=false` | lost: written, never read back | `false`, reason `auto_recover_off` |

This covers servers declared in `config.yaml` and servers registered over the
REST API alike. Deleting the policy is kept the same way, so a restart does not
bring back a policy the operator removed.

Two failures at start are not covered by the table. A stored policy that no
longer parses fails closed: the server denies every tool until the operator
delivers its policy again, and the gateway logs
`l7_policy_unreadable_denying_all` at error. A database that cannot be read at
start fails open: the gateway starts anyway, logs the failure at error, and
serves its `config.yaml` servers without the stored policies until the operator
delivers them again.

**To keep the policy across restarts,** select a durable backend in the chart:

```yaml
persistence:
  backend: postgresql
```

or `backend: sqlite` with `persistence.sqlite.persistentVolume.enabled: true`.
Multiple replicas need PostgreSQL in any case.

**To find a deployment that does not keep it,** look for
`l7_policy_not_persisted` in the gateway log, or for `"persisted": false` in the
response the operator receives. Both appear on every push, so a deployment that
has one has it on every reconcile.
