**core:** a `coordination:` block with no `persistence.backend` is refused at
startup instead of booting into the failure the block exists to prevent. The
check only refused a backend that could not be shared and returned early when no
backend had been selected at all -- but with no backend there is no lease keeper
either, so `may_manage()` is True in every process, every replica runs the
management loops and every replica reports `manages_fleet: true`. A deployment
still on the legacy per-subsystem keys (`event_store.driver: postgresql`,
`auth.storage.driver: postgresql`) shares one database and declares
coordination, and was never asked the question. Such a configuration now fails
the boot with a message naming the missing decision: set
`persistence.backend: postgresql`, or remove the `coordination:` block to run as
a single gateway.
