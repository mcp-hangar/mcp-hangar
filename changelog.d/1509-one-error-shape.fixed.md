**core:** the two error producers #1495 left behind now answer the one shape.
`MCPError.to_dict()` rendered a fifth key, `type`, naming the exception class: a
second serializer in the domain that nothing reached, since the REST envelope
builds `code`/`message`/`details` from the exception itself and a tool error is
`ToolErrorPayload`'s three keys. It is deleted rather than corrected -- an
unreachable serializer is what drifts unnoticed. `hangar_reload_config` answered
a failed reload with `status`/`message`/`error_type`, the right key in a layout
of its own; it now answers `error`, `error_type` and `details`, like every other
tool failure, with the same message text under `error`. A successful reload is
unchanged. A tree-wide test fails if a new error payload names the failure under
`type` again. See `UPGRADE.md`.
