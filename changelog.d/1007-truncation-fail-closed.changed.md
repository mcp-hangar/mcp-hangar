**core:** `truncation.cache_driver: redis` fails closed. A missing `redis`
package, an unparseable URL, or a server that cannot `SETEX` (a Sentinel
listen port answers PING and fails every data command) used to fall back to
the per-replica memory cache while the log still said `cache_driver=redis` --
so cross-replica continuation fetches missed and nobody was told. Now: init
failures refuse the boot, the constructor probes with `SETEX` (not PING), the
boot log names the ACTUAL backend, and a truncated response only advertises a
`continuation_id` when the full payload was stored. A new `redis` extra
(`pip install mcp-hangar[redis]`) ships in the published image next to
`[postgres]`; `cache_driver: memory` on a coordinated deploy stays legal and
logs a per-replica warning. See UPGRADE.md
