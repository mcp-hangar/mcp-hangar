### A gate refusal's text has a new first line

The message a gate wrote now starts on the second line of `error` and of the tool result's text. Anything matching a refusal's text from its start has to read `_meta["io.mcp-hangar/toolFailure"]` instead, or the second line. `error_type` is unchanged. Do not treat the text as proof Hangar refused: only the `_meta` key will be, once upstream results are stripped of it.
