### A configuration write that lost a race between replicas is now refused

Updates and L7 policy deliveries for a server are now compare-and-set writes on its stored configuration row. A write is refused when another replica changed or deleted that row after this replica read it. The refusal is a `ConcurrentModificationError`, which the REST API answers as a 500 until it gets its own status. The operator's client retries 5xx, so a refused policy delivery is retried and applied on top of the newer row. Other clients should retry a refused update the same way.

A create now refuses a name whose row is live, even on a replica that has not yet heard of that server. It answers "already exists" instead of overwriting the row.

During a rolling upgrade, replicas on the previous release still write unversioned and can still overwrite the row. The protection holds once every replica runs this release. Single-replica deployments are not affected.
