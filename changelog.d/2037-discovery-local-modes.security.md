**security:** labels and annotations can no longer choose a process or host mounts
for Hangar to launch. A pod annotated `mcp-hangar.io/mode: subprocess` used to
register a subprocess server that ran `mcp-hangar.io/command` on the gateway host,
so anyone who could create a pod in a watched namespace chose a command the gateway
ran. The Kubernetes source now refuses every mode other than `http`, `sse` and
`remote` unless it sets `allow_local_modes: true`, and logs
`kubernetes_discovery_local_mode_refused` once per name. A Docker container in
`container` mode used to be run with the host mounts named in `mcp.hangar.volumes`,
read-write and without a read-only root. Container mode stays on by default, but a
`mcp.hangar.volumes` label is now honoured only for absolute host paths under the
source's new `allowed_mount_paths` (never `/` or a `*.sock` file). A container whose
label names anything else is refused and logged once as
`docker_discovery_label_volumes_refused`. A discovered container also gets a
read-only root unless labelled `mcp.hangar.read-only: "false"`. The shared container
volume check now refuses the host root `/` (and paths such as `/etc/..` that
normalise to it), for declared servers too. Remote discovery is unchanged.
