### a reload removes only the servers the file owns

A configuration reload (`POST /api/config/reload`, `hangar_reload`, SIGHUP or a
saved `config.yaml`) used to stop and remove every server the file did not
declare, until the next restart brought it back. It now removes only what the
file declared, so anything that used a reload to clear such a server has to
remove it instead:

```bash
curl -X DELETE http://localhost:8000/api/mcp_servers/<id>
```

A server the file declares is unaffected, and it is still removed by deleting
it from the file.
