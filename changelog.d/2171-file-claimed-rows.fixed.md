**core:** a server the configuration file declared, and that was edited at
runtime (an L7 policy push, a `PUT /api/mcp_servers/{id}`), no longer comes
back as a registered server after the file drops it. The write stored a row
that recovery read as a registration once the file stopped declaring the id,
so a restart restored the server the file had removed. A row created for a
server the file declares is now claimed by the file: recovery still carries
its L7 policy onto the server while the file declares it, and skips the row,
with a `recovery_skipped_row_a_file_claimed` warning, once the file does not.
`POST /api/mcp_servers` or a discovery registration under that id replaces
the row instead of answering "already exists", and keeps its L7 policy
(`l7_policy_carried_from_claimed_row`) but not its tool access policies.
Rows written before this release are not claimed yet
