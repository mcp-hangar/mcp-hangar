**core:** a discovery source in `mode: authoritative` now removes a server it no
longer reports once that server's TTL has run out, as documented. It never did:
the lost name was checked once, before its TTL could expire, and every cycle
refreshed the TTL of every server discovery still held, so a deleted container,
pod or file stayed registered until the gateway restarted. The removal goes
through `DeleteMcpServerCommand`, records `McpServerDiscoveryLost` with reason
`ttl_expired` (which a TTL expiry never recorded before), and the removed
server is not re-created by the next cycle. A server that comes back before its
TTL keeps running, a source whose scan fails removes nothing, and `additive`
sources and static configuration are unchanged
