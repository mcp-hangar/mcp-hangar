**core:** an in-memory SQLite event store (`SQLiteEventStore(":memory:")`) no
longer returns wrong results, or fails with `InterfaceError: bad parameter or
other API misuse`, when threads read and append at the same time. The store
keeps one connection for its whole life, and its reads used that connection
without the lock its appends hold. On Python 3.12 and later two threads could
then drive the same prepared statement, and a read came back short, with
`NULL` values, or failed. On every version a read could run inside another
thread's open append and return events that the append then rolled back.
Every read on the shared connection now runs under the store's lock and
returns fully fetched rows. File-backed stores open a connection per call and
are unchanged.
