**core:** groups and members made through `/api/groups` survive a restart on a
durable backend. They are stored beside the server rows, in new tables on SQLite
and PostgreSQL, and restored at bootstrap before anything is served, so a server
added to a group over REST is under its group's policy from the first call and
listing. The file wins for what it declares, by the rules a reload uses. A
create, update, delete or member change is written to the store first, and fails
if that write fails; deleting a group or member removes its row. If the stored
groups cannot be read, the gateway does not start. On the in-memory backend a
group still lasts as long as the process.
