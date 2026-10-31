### groups and members made over REST come back after a restart

A group created with `POST /api/groups`, and a member added with
`POST /api/groups/{id}/members`, used to last until the next restart, which went
back to the groups in the file. On a durable backend they are now stored and
restored, so nothing needs doing to keep them. A group made over REST after this
upgrade returns at each restart until it is deleted over REST: if a deployment
relied on a restart to clear one, delete it instead.

The new tables are created at startup. Groups made before the upgrade were not
stored, so they are gone after the restart that upgrades, as before: make them
again if they are wanted. An older release ignores the tables.

A gateway whose stored groups cannot be read now refuses to start, as it already
does when the peer-key name record cannot be read.

With `MCP_AUTO_RECOVER=false` the stored groups stay unread, as the stored servers do.

A row made before the file declared the same group or member stays when the file
drops it again, so a restart brings it back where a reload would remove it. After
that restart, delete it over REST.
