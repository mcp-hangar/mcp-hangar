**core:** a remote server and discovery built with `HangarConfig` now take
effect. The builder wrote a remote server's `url=` as `url`, but the gateway
reads a remote server's address from `endpoint`, so the server booted with no
address. `enable_discovery()` wrote `discovery.docker`, `discovery.kubernetes`
and `discovery.filesystem`, but the gateway reads `discovery.enabled` and
`discovery.sources`, so discovery never turned on. The builder now writes
`endpoint`, and one `additive` source per requested discovery type.
`Hangar.start()` now also starts discovery when the configuration enables it,
from a file or from the builder, and `Hangar.stop()` stops it. Before, bootstrap
built the orchestrator and nothing under the facade ran it.

`build()` checks the result against the config schema and refuses a key the
gateway does not read. The builder now refuses these, which it used to store
and the gateway never read:

- more than one `filesystem` directory, since the gateway keeps one source per
  type
- `enable_discovery()` with no source
- `mode="group"`
- an option the server's mode does not read
- `set_intervals()`

`to_dict()` no longer includes the facade's `max_concurrency`.
