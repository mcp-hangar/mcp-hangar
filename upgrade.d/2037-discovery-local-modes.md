### Discovery no longer takes a host process or host mounts from a label

#### Kubernetes: local modes need `allow_local_modes`

The Kubernetes discovery source now registers only pods Hangar connects to: `mcp-hangar.io/mode` `http` (the default), `sse` or `remote`. A pod whose annotation asks for anything else, such as `subprocess` with `mcp-hangar.io/command`, which ran that command on Hangar's own host, is skipped and logged once as `kubernetes_discovery_local_mode_refused`.

If whoever can create pods in the watched namespaces is trusted to choose what Hangar runs on its own host, opt the source in. The value must be the boolean `true`; a string such as `"yes"` leaves the source closed.

```yaml
discovery:
  sources:
    - type: kubernetes
      allow_local_modes: true
```

#### Docker: label volumes need `allowed_mount_paths`, and the root is read-only

Docker `container` mode is unchanged and still the default. Two things about the container Hangar runs from it are different:

- A `mcp.hangar.volumes` label is honoured only when every host path in it is absolute and under one of the source's `allowed_mount_paths`. `/` and any `*.sock` file are refused even there, and `~` is not expanded. A container whose label names anything else is not registered, rather than registered without its mounts, and is logged once as `docker_discovery_label_volumes_refused`. With no `allowed_mount_paths`, which is every existing configuration, any container carrying the label is refused. List the directories it may use:

  ```yaml
  discovery:
    sources:
      - type: docker
        allowed_mount_paths: ["/srv/mcp-data"]
  ```

- A discovered container runs with a read-only root filesystem, as a declared one does. Label it `mcp.hangar.read-only: "false"` to get the old behaviour.

Containers without a volumes label, `http` and `sse` containers, filesystem and entry-point discovery, and servers declared in `config.yaml` are not affected by either change.

#### Already-registered servers stay

Servers already registered from these labels before the upgrade are stored and are not removed by it. In `GET /api/mcp_servers` they carry the description `Discovered from kubernetes` or `Discovered from docker` (`GET /api/mcp_servers/<id>` shows `provenance: discovery`); check the `subprocess` and `docker` ones and `DELETE /api/mcp_servers/<id>` each one you did not intend.

#### Every container: no host root mount

A container mount whose host path is `/`, or normalises to it (`/etc/..`), is now refused for every container server, declared ones included. Every other declared mount is checked exactly as before: a trailing slash or a doubled slash (`/var/data/`, `/var//data`) is not normalised by the other rules.
