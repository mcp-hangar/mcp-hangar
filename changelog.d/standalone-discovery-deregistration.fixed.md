**core:** a gateway that keeps its fleet in storage no peer shares
(`persistence.backend: sqlite`, or the older `MCP_PERSISTENCE_ENABLED`) never
removed a server discovery had registered. When the source stopped reporting it,
or its TTL ran out, the deletion was refused as if this gateway had lost a
management lease, which a gateway on such storage never holds. The server stayed
in the fleet and in its row, and came back on every restart. Such a deletion is
now fenced only on storage peers share, decided at each write. On PostgreSQL a
gateway that holds no lease still refuses it. A server an authoritative source
left behind this way is taken back at the next start and removed one TTL later,
with no action needed.
