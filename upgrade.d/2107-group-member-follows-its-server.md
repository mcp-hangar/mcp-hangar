### A deleted server's group member is vacant until discovery brings its server back

Deleting a server that is a member of a group (`DELETE /api/mcp_servers/{id}`,
or discovery removing it when its TTL runs out) no longer leaves the group
routing to it. Before, a call through the group selected the stopped copy and
started the deleted server's command or image again, outside the fleet.

What you may notice:

- The member stays in the group, with `"vacant": true`, `"state": "cold"` and
  `"in_rotation": false` in the group status (`GET /api/groups/{id}`,
  `hangar_group_list`). It still counts in `total_members`, as the stopped copy
  did, and no longer counts as in rotation.
- A group whose only member is deleted now goes `inactive` and refuses calls,
  where it used to start the deleted server.
- The group's state follows: a group with `min_healthy` equal to its members
  in rotation reads `partial` after one of them is deleted, where it read
  `healthy` because the stopped copy still counted. An alert on group state
  can fire on it.
- When discovery registers a server under the same id again, it goes back in
  the group with the weight, priority and rotation the member had: the case
  of a discovered server that drops out and comes back.
- Re-creating a server over the API leaves the member vacant; re-add it with
  the group API (`POST /api/groups/{id}/members`). Deleting and re-creating a
  server is not a way to change a group's members. A key-pinned server never
  fills a member either, as it cannot be added to a group.
- To take the member out of the group for good, remove it with
  `DELETE /api/groups/{id}/members/{member_id}`.

Servers the configuration file declares cannot be deleted over the API, and a
reload never leaves a vacant member: a reload whose group names a server the
file no longer declares is refused, as before.
