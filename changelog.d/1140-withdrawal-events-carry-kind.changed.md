**core:** `ToolWithdrawn` and `ToolRestored` carry `kind` (`tool`, `prompt` or
`resource`) at schema version 2, so a consumer rebuilding the withdrawal overlay
from the event log can tell which surface was withdrawn. The admin endpoints
still write `tool`; rows stored by an older gateway have no `kind` and replay
as `tool`, which is all they could have been
