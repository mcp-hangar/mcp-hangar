### A failed namespace or unreadable discovery file no longer removes its servers

On an authoritative source, two cases that used to count as a real absence
now keep what they registered, as a scan that fails as a whole already did:

- a Kubernetes namespace whose pod listing fails (forbidden, timeout, API
  error) while the API itself answers;
- a discovery file that still exists but cannot be read or parsed.

Such a server is neither removed nor refreshed while its part keeps failing,
and every cycle logs `discovery_part_failed_servers_kept` at WARNING with the
part, the servers kept and those already past their TTL. Once the part answers
again, a server that is no longer there is removed straight away.

What this changes for a setup that relied on the old behaviour:

- Revoking Hangar's permission to list pods in a namespace no longer removes
  that namespace's servers; they stay, with the warning, until you also drop
  the namespace from the source's `namespaces` list.
- Deleting a namespace depends on how Hangar may list pods. With a
  ClusterRole and ClusterRoleBinding (as in
  `examples/discovery/kubernetes-deployment.yaml`) a deleted namespace lists
  as empty and its servers are removed after their TTL, as before. With a
  Role and RoleBinding per namespace, deleting the namespace deletes the
  RoleBinding too, the listing is forbidden from then on, and its servers are
  kept with the warning: drop the namespace from `namespaces` to let them go.
- Breaking a discovery file no longer removes its server; delete the file or
  set `enabled: false` in it to remove the server.
- With `namespaces` unset, a failure to list the namespaces makes the whole
  scan fail, so nothing is removed that cycle (it used to read as every pod
  gone).

Unchanged: a deleted file, a disabled definition and a file refused by the
trust checks (world-writable, outside the discovery path) still remove the
server after its TTL. Additive sources never remove anything.
