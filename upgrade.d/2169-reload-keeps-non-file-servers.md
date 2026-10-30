### a reload removes only the servers the file owns

A configuration reload (`POST /api/config/reload`, `hangar_reload`, SIGHUP or a
saved `config.yaml`) used to stop and remove every server the file did not
declare, until the next restart brought it back. It now removes only what the
file declared, so anything that used a reload to clear such a server has to
remove it instead:

```bash
curl -X DELETE http://localhost:8000/api/mcp_servers/<id>
```

A reload now also keeps the groups and group members made over REST, with their
policies. A group the file declares takes its strategy and settings from the
file and keeps the members added with `POST /api/groups/{id}/members`, unless the
file now names them; a member whose server left the fleet leaves its group. To
remove one, use `DELETE /api/groups/{id}/members/{member_id}` or
`DELETE /api/groups/{id}`. These groups and members are not stored, so a restart
still starts from the file's.

A server the file declares is unaffected, and it is still removed by deleting
it from the file. A group the file stops declaring is removed, and the servers
in it keep running.
