### `mcp-hangar add` refuses to replace a server the config already has

Re-running `mcp-hangar add <name>` for a server already in the file used to
overwrite its entry with the registry default, dropping its pins, policy, args and
`env`. It now leaves the file alone:

- if the entry is exactly what `add` would write, it prints that and exits 0, so
  scripts that re-run `add` keep working;
- otherwise it exits 1 and lists the keys the replacement would drop or change.

To get the old behaviour, pass `--replace`:

```bash
mcp-hangar add git --replace
```
