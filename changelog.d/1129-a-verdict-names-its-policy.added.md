**core:** an egress verdict now names the policy that produced it.
`L7Policy.policy_id` is a content hash of the compiled rules (`sha256:` plus 16
hex digits), carried by the `Decision`, by `EgressPolicyViolationObserved`, by
the deny and approval-required refusals, and by `EgressPolicySet` -- so "which
policy decided this call" and "when did that policy change" join on a value
rather than on adjacent timestamps. `GET /api/mcp_servers/{id}/l7_policy`
returns it as `policyId`, so an id in an audit record can be resolved back to
the rules. A hash rather than an assigned name because the sources have nothing
in common to assign from: an operator-compiled policy has a Kubernetes
`resourceVersion` upstream, one from `config.yaml` or the REST channel has no
identity at all, and the hash is also stable across restarts and replicas. It
covers `mode`, so an Audit-to-Enforce flip is a different id
