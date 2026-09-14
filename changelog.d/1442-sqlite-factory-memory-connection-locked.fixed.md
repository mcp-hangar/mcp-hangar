**core:** an in-memory store built on `SQLiteConnectionFactory` -- the
metrics history store is one by default, when no persistence backend is
selected -- now runs every use of its one shared connection, reads included,
one thread at a time. The factory keeps a single connection for `:memory:`,
because each new connection there is a new, empty database, and it handed that
connection to every thread without taking its lock. On Python 3.12 and later,
two threads on the connection could drive the same prepared statement. A
`query()` then came back short, with `NULL` values, or failed with
`InterfaceError: bad parameter or other API misuse`. On every version, a read
could see a snapshot half written, and commit it halfway. `get_connection()`
now holds a re-entrant lock for the caller's whole block on that connection,
and `close()` waits for it. File-backed stores keep one connection per thread
and are unchanged.
