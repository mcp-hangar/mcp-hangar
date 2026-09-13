**core:** shutdown now cancels the commands that sagas scheduled on a timer.
`SagaManager.cancel_all_scheduled_commands()` existed and nothing called it, so
a recovery retry armed before shutdown could fire during it and start a server
that was being stopped. `ServerLifecycle.shutdown` cancels them first, and
`ApplicationContext.shutdown` cancels again once its workers have stopped.

Shutdown also stops the background loops that bootstrap starts for the fleet
writer and the fleet projection, which nothing stopped before.
`SqliteBackend.close()` no longer leaves a loop running for every adapter it
closes, and a loop whose owner is dropped without `close()` now stops when the
owner is garbage-collected. `MetricsSnapshotWorker.stop()` now ends its thread
at once, not up to one interval later.
