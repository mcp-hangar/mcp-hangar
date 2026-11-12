### a server the file dropped no longer comes back after a restart

- A server the configuration file dropped no longer comes back after
  a restart. Its row is kept, claimed by the file, and counted in
  `GET /api/system` as `instance.dormant_file_claimed_rows`.
- A row of a server dropped from the file before the upgrade was never
  claimed, and nothing claims it now, so the server still comes back at a
  restart, and at every one after it. Remove it after that restart with
  `DELETE /api/mcp_servers/{id}`. Before the restart the same call answers 404,
  because the server is not in the fleet.
- Rolling back a file that took over a server created over the API no longer
  restores that server. Create it again with `POST /api/mcp_servers`, which
  replaces the claimed row and keeps its L7 policy.
