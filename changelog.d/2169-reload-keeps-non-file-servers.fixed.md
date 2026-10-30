**core:** a configuration reload removes only the servers the file owns. It
counted every server in the fleet that the file did not name as deleted from
it, so a server created with `POST /api/mcp_servers` or registered by discovery
was stopped and dropped with its policies until a restart brought it back, and
a discovery rescan did not. Such a server now stays, running and untouched. A
file that newly declares its id still takes it over, as at startup, and a server
removed from the file is still stopped and removed.
