### Subprocess `args` are now passed to the server

For `mode: subprocess`, `args` is appended to `command`. Before, it was silently ignored, so a hand-written `args` on a subprocess server starts doing something. If you kept an `args` there that the server must not receive, remove it. `args` for `mode: docker` is unchanged.
