# The governed-deny recording

The 20-second terminal recording at the top of the repository README, and the
script that regenerates it.

> **Needs a core carrying the #1231 fix.** Before it, a `front_door` gateway
> answered `tools/list` before its upstreams were warm, so this demo only
> worked if the client polled for the catalogue first — and a recording that
> depends on the client papering over a bug is a recording of something the
> reader cannot reproduce. The gateway waits for its own warm-up now, so
> `client.py` is the plain thing it should always have been.

```bash
cd examples/rugpull/recording
vhs demo.tape          # writes ../../../.github/demo/governed-deny.gif
```

Needs [`vhs`](https://github.com/charmbracelet/vhs) on `PATH`, plus
`mcp-hangar` and a `python` carrying the `mcp` SDK — the same two things the
quickstart needs.

## Why a tape and not a screen capture

The recording shows real output from a real gateway: `vhs` types the commands
into a PTY and captures what actually comes back. Nothing in the frame is
typeset by hand, which is the point — a recording of a deny that never happened
would be a fabricated record of the one behaviour this project exists to prove.

Keeping the tape beside the asset means the recording can be **regenerated**
when the CLI's output changes, instead of quietly becoming a picture of a
version nobody runs any more. That failure mode is the reason this directory
exists rather than a GIF someone made once on a laptop.

## What is in the frame, and what is not

On camera: the pinned call is allowed, the server rewrites its own tool
description, and the identical call is refused by Hangar before the server is
asked.

Off camera, in the tape's `Hide` block: `mcp-hangar pin --config demo.yaml
--write`. That step is real and required — the deny is measured against the pin
it writes — but `pin` emits a dozen `[info]`/`[debug]` stdio-client lines that
no flag suppresses (`--quiet`, `MCP_LOG_LEVEL` and `logging.level` are all
ignored on that path), and a twenty-second asset cannot spend six seconds on
them.

`client.py` is a small MCP client standing in for Claude Code, Cursor or Claude
Desktop; it is what makes the call you see. It connects, calls, and prints the
verdict — no retries, no waiting for the catalogue, nothing that a real client
would not do. That is deliberate: anything it had to compensate for would be a
defect the recording was hiding rather than showing.
