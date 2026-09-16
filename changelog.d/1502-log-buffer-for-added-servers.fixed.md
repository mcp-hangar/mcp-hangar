**core:** a configuration reload now gives a server it adds a log buffer.
Per-server buffers were attached once, at boot, so a server a reload ADDED had
none: its stderr reader was never started, and `GET
/api/mcp_servers/{id}/logs` served an empty list for a running server. The
commit attaches one as it puts the server in the repository, through the helper
bootstrap uses, and logs `log_buffer_attached_to_mcp_server`. A server the
reload removes has its buffer released rather than left registered under an id
that is now free, while a server the file does not declare and that is still
running keeps its own. A rebuilt server still carries its predecessor's buffer
over.
