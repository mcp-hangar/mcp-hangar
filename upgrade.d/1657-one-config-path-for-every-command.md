### Every command reads the configuration file by one rule

`init`, `status`, `add`, `remove`, `pin`, `serve`, a bare `mcp-hangar`,
`config check` and `python -m mcp_hangar.server` now resolve the configuration
file the same way, highest first:

1. a path on the command line -- the command's own flag or argument, then the
   global `--config`;
2. `$MCP_CONFIG`;
3. `./config.yaml`, if the working directory has one;
4. `~/.config/mcp-hangar/config.yaml`, the file `init` writes.

What changes for you:

- **`serve` with no flag, no `MCP_CONFIG` and no `./config.yaml`** used to boot
  the built-in demo config. It now reads `~/.config/mcp-hangar/config.yaml` if
  `init` wrote one, and refuses to start if there is none (see "A missing
  configuration file stops the gateway"). A `./config.yaml` beside the process still wins, so a setup
  that ran `serve` next to its file is unchanged, and so is the container image
  (its working directory is `/app`).
- **`init`, `add`, `remove` and `status` in a directory with a
  `./config.yaml`** used to write or report `~/.config/mcp-hangar/config.yaml`.
  They now use `./config.yaml`, the file `serve` there reads. `init -y` backs
  that file up and replaces it, and `add` and `remove` edit it in place -- so
  run them in a directory whose `config.yaml` is Hangar's, or pass
  `--config ~/.config/mcp-hangar/config.yaml` (or `init --config-path`) to keep
  the old target.
- **`status` with a named file that does not exist** used to report whichever
  default file it found next. It now reports that no configuration was found at
  the named path.
- **`MCP_CONFIG` no longer outranks a global `--config`.**
  `MCP_CONFIG=a.yaml mcp-hangar --config b.yaml serve` used to run `a.yaml`,
  because `serve`'s own `--config` read the variable. It now runs `b.yaml`.
- **`pin` and `config check` honour the global `--config`.**
  `mcp-hangar --config X pin --check` used to exit 2 looking for
  `./config.yaml`; it now checks `X`.
- **Reload works on a gateway started without `--config`.** The watcher,
  SIGHUP, the reload tool and `POST /api/config/reload` used to fail with "No
  configuration path specified"; they now reload the file the gateway booted
  from.
