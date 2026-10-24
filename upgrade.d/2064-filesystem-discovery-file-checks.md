### Filesystem discovery skips world-writable definition files and symlinks out of its directory

A `filesystem` discovery source now skips a definition file, and logs
`filesystem_discovery_file_refused` once with the reason, when any of these
holds:

- the file resolves, through a symlink, to a path outside the source's `path`;
- the file is not a regular file;
- on POSIX, the file is world-writable;
- on POSIX, the source's `path`, or a directory between it and the file, is
  world-writable. A sticky directory such as `/tmp` counts, since anyone can
  still add a file to it.

Group-writable files and directories, files owned by another user, and a
mounted Kubernetes ConfigMap are unaffected. If the warning appears, move the
file into the directory or remove world write:

```bash
chmod o-w /etc/mcp-hangar/mcp_servers.d /etc/mcp-hangar/mcp_servers.d/*.yaml
```

A server the source registered before the upgrade may stay registered: review
the servers described as `Discovered from filesystem` and delete any you did not
expect. There is no setting to turn the checks off.
